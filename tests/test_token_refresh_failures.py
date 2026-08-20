"""A failed token refresh must not look like a revoked account.

THE BUG, found while diagnosing six production stream stoppages that had no
recorded cause. get_valid_twitch_token ended in:

    try:
        tokens = await twitch_oauth.refresh_access_token(refresh)
    except Exception:
        return None

Every failure produced the identical silent None — a revoked token, a DNS
blip, a 500 from Twitch, a timeout. The caller turns None into
TwitchAuthExpiredError, which STOPS EVERY STREAM the user has and tells them
their Twitch connection expired and to sign out and back in. So one bad HTTPS
call to Twitch could end a user's session across every channel they monitor,
and leave nothing in the log to say why.

The two cases are now separated by what Twitch actually said:

  400/401  -> Twitch answered, and the answer is that this refresh token is
              finished. That is a real re-login. Returns None, stream stops.
  anything -> we could not complete the refresh. The token may be perfectly
  else       fine. Raises TwitchTokenTransientError so the job retries and the
             stream keeps running.
"""

import asyncio

import pytest


@pytest.fixture
def store(monkeypatch, tmp_path):
    import time
    from src.auth import users as user_store

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")

    # An expired access token with a refresh token stored — the state that
    # sends every call down the refresh path.
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: {
        "id": uid, "tw_access": "enc-access", "tw_refresh": "enc-refresh",
        "tw_expires_at": time.time() - 60})
    monkeypatch.setattr(user_store, "_decrypt", lambda v: "plain-" + (v or ""))
    monkeypatch.setattr(user_store, "_store_refreshed_tokens",
                        lambda *a, **k: None)
    return user_store


def _raiser(exc):
    async def _f(refresh):
        raise exc
    return _f


class _HttpError(Exception):
    """Stands in for aiohttp.ClientResponseError, which carries .status."""
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status = status


# ── permanent: Twitch says the refresh token is dead ─────────────────────────

@pytest.mark.parametrize("status", [400, 401])
def test_a_rejected_refresh_token_returns_none(store, monkeypatch, status):
    """This one really is a re-login, and stopping the stream is right."""
    from src.auth import twitch_oauth
    monkeypatch.setattr(twitch_oauth, "refresh_access_token", _raiser(_HttpError(status)))
    assert asyncio.run(store.get_valid_twitch_token("u1")) is None


def test_no_refresh_token_stored_returns_none(store, monkeypatch):
    import time
    monkeypatch.setattr(store, "get_by_id", lambda uid: {
        "id": uid, "tw_access": "enc", "tw_refresh": "",
        "tw_expires_at": time.time() - 60})
    monkeypatch.setattr(store, "_decrypt", lambda v: "" if v == "" else "plain")
    assert asyncio.run(store.get_valid_twitch_token("u1")) is None


def test_an_unlinked_account_returns_none(store, monkeypatch):
    monkeypatch.setattr(store, "get_by_id", lambda uid: {"id": uid})
    assert asyncio.run(store.get_valid_twitch_token("u1")) is None


# ── transient: we could not complete the refresh ─────────────────────────────

@pytest.mark.parametrize("exc", [
    _HttpError(500), _HttpError(502), _HttpError(503), _HttpError(429),
    TimeoutError("timed out"),
    ConnectionResetError("connection reset"),
    OSError("temporary failure in name resolution"),
])
def test_a_transient_failure_raises_instead_of_returning_none(store, monkeypatch, exc):
    """THE regression. Each of these used to return None, which stopped every
    one of the user's streams and told them to re-login."""
    from src.auth import twitch_oauth, users
    monkeypatch.setattr(twitch_oauth, "refresh_access_token", _raiser(exc))
    with pytest.raises(users.TwitchTokenTransientError):
        asyncio.run(store.get_valid_twitch_token("u1"))


def test_the_transient_error_is_not_an_auth_expiry(store):
    """The processor branches on TwitchAuthExpiredError to stop the stream. If
    the transient error were a subclass, the fix would do nothing at all."""
    from src.auth.users import TwitchTokenTransientError
    from src.processor.clip_processor import TwitchAuthExpiredError
    assert not issubclass(TwitchTokenTransientError, TwitchAuthExpiredError)


def test_the_original_cause_is_kept(store, monkeypatch):
    """Raised `from exc`, so the traceback the processor logs still names the
    real failure rather than stopping at our own wrapper."""
    from src.auth import twitch_oauth, users
    boom = _HttpError(503)
    monkeypatch.setattr(twitch_oauth, "refresh_access_token", _raiser(boom))
    with pytest.raises(users.TwitchTokenTransientError) as ei:
        asyncio.run(store.get_valid_twitch_token("u1"))
    assert ei.value.__cause__ is boom


# ── the happy path still works ───────────────────────────────────────────────

def test_a_successful_refresh_returns_the_new_token(store, monkeypatch):
    from src.auth import twitch_oauth

    async def _ok(refresh):
        return {"access_token": "fresh", "refresh_token": "r2", "expires_in": 3600}
    monkeypatch.setattr(twitch_oauth, "refresh_access_token", _ok)
    assert asyncio.run(store.get_valid_twitch_token("u1")) == "fresh"


def test_an_unexpired_token_is_returned_without_refreshing(monkeypatch, tmp_path):
    import time
    from src.auth import users as user_store, twitch_oauth

    monkeypatch.setattr(user_store, "get_by_id", lambda uid: {
        "id": uid, "tw_access": "enc", "tw_refresh": "encr",
        "tw_expires_at": time.time() + 3600})
    monkeypatch.setattr(user_store, "_decrypt", lambda v: "live-token")

    async def _boom(refresh):
        raise AssertionError("refreshed a token that had not expired")
    monkeypatch.setattr(twitch_oauth, "refresh_access_token", _boom)
    assert asyncio.run(user_store.get_valid_twitch_token("u1")) == "live-token"


def test_a_200_with_no_access_token_returns_none(store, monkeypatch):
    """Twitch answered successfully and gave us nothing usable. Not transient —
    retrying will get the same empty answer."""
    from src.auth import twitch_oauth

    async def _empty(refresh):
        return {"refresh_token": "r2", "expires_in": 3600}
    monkeypatch.setattr(twitch_oauth, "refresh_access_token", _empty)
    assert asyncio.run(store.get_valid_twitch_token("u1")) is None


# ── it is no longer silent ───────────────────────────────────────────────────

def test_every_failure_path_logs(store, monkeypatch):
    """The whole reason six stoppages had no cause. Each distinct failure must
    leave a distinct, greppable line."""
    from src.auth import twitch_oauth, users

    seen = []

    # Mirrors stdlib logging's signature, which is what _ulog actually is:
    # warning(msg, *args) with %-style formatting. An earlier version of this
    # fake took (event, **kw) — structlog's shape — and passed while the real
    # logger would have raised TypeError on the same call.
    class _Log:
        def warning(self, msg, *args, **kw):
            seen.append(msg % args if args else msg)

        def error(self, msg, *args, **kw):
            seen.append(msg % args if args else msg)

        def debug(self, *a, **kw):
            pass
    monkeypatch.setattr(users, "_ulog", _Log())

    monkeypatch.setattr(twitch_oauth, "refresh_access_token", _raiser(_HttpError(401)))
    asyncio.run(store.get_valid_twitch_token("u1"))

    monkeypatch.setattr(twitch_oauth, "refresh_access_token", _raiser(_HttpError(503)))
    with pytest.raises(users.TwitchTokenTransientError):
        asyncio.run(store.get_valid_twitch_token("u1"))

    assert any("twitch_refresh_rejected" in l for l in seen), seen
    assert any("twitch_refresh_failed_transient" in l for l in seen), seen


def test_no_ulog_call_passes_structlog_style_keywords():
    """_ulog is stdlib logging, not structlog, and stdlib Logger.warning
    rejects arbitrary keywords with TypeError.

    Two calls in this module were already written structlog-style and would
    have raised — inside the corrupt-users.json handler, i.e. exactly when
    something had already gone wrong. Most of the module uses %-style, so the
    mistake is easy to repeat; this pins it. Only logging's own keywords are
    allowed through.
    """
    import ast
    import pathlib

    ALLOWED = {"exc_info", "stack_info", "stacklevel", "extra"}
    tree = ast.parse(pathlib.Path("src/auth/users.py").read_text())
    bad = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_ulog"):
            for kw in node.keywords:
                if kw.arg not in ALLOWED:
                    bad.append(f"line {node.lineno}: {kw.arg}=")
    assert bad == [], (
        "these _ulog calls pass keywords stdlib logging cannot take, and raise "
        f"TypeError when they fire: {bad}")


def test_the_bare_except_return_none_is_gone():
    """A structural guard on the exact shape of the bug: catching everything
    and answering None makes all failures indistinguishable again."""
    import inspect
    from src.auth import users
    src = inspect.getsource(users.get_valid_twitch_token)
    body = src[src.index("refresh_access_token"):]
    assert "except Exception:\n        return None" not in body, \
        "the silent catch-all is back — every failure looks like a revoked token"
    assert "_ulog" in body, "the refresh failure path stopped logging"


# ── the caller does the right thing with each ────────────────────────────────

def test_the_processor_stops_the_stream_only_on_a_real_expiry():
    """main.py has a branch for TwitchAuthExpiredError that stops the stream and
    tells the user to re-login. The transient error must NOT reach it — it falls
    to the generic handler, which retries on the next moment."""
    import inspect
    import src.main as main
    src = inspect.getsource(main.run_clip_processor)
    assert "except TwitchAuthExpiredError:" in src
    assert "TwitchTokenTransientError" not in src, \
        "the transient error is handled as an auth expiry, which stops the stream"


def test_only_the_processor_asks_for_a_token():
    """The raise is safe because there is exactly one caller. A second one that
    does not expect an exception would break on the first Twitch blip."""
    import pathlib
    hits = []
    for p in pathlib.Path("src").rglob("*.py"):
        text = p.read_text()
        if "get_valid_twitch_token" in text and "def get_valid_twitch_token" not in text:
            hits.append(str(p))
    assert hits == ["src/processor/clip_processor.py"], \
        f"get_valid_twitch_token gained callers that may not handle the raise: {hits}"
