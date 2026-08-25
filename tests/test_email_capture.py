"""Collecting an email address, and being honest about who we have one for.

THE CONSTRAINT THAT SHAPES ALL OF THIS: a token's OAuth scopes are fixed when
it is issued and a refresh returns the same set. Every Twitch token stored
before user:read:email was requested will return no email however many times we
ask, so existing accounts CANNOT be backfilled from Twitch — they hand it over
the next time they sign in and approve the new consent screen.

Stripe is the opposite: a billing email can be read at any time, for anybody who
has ever paid. So the reachable population splits cleanly, and the product has
to say which side each account is on rather than implying it has everybody.
"""

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch):
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    return users


# ── the scope ────────────────────────────────────────────────────────────────

def test_the_email_scope_is_requested():
    from src.auth.twitch_oauth import _SCOPES, authorization_url
    assert "user:read:email" in _SCOPES
    # And it reaches the consent screen, which is the only place it counts.
    assert "user%3Aread%3Aemail" in authorization_url("state123")


def test_clip_creation_is_not_traded_away_for_it():
    """clips:edit is what the product runs on. An email is worth nothing if
    adding it dropped the permission that makes clips."""
    from src.auth.twitch_oauth import _SCOPES, authorization_url
    assert "clips:edit" in _SCOPES
    assert "clips%3Aedit" in authorization_url("s")


def test_get_user_reads_the_email_when_twitch_sends_one():
    """Helix omits the field entirely without the scope — .get, not [], or
    every sign-in on an old grant raises instead of signing in."""
    import asyncio
    from unittest.mock import patch, MagicMock
    from src.auth import twitch_oauth

    class _Resp:
        def __init__(self, payload): self._p = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def raise_for_status(self): pass
        async def json(self): return self._p

    class _Sess:
        def __init__(self, payload): self._p = payload
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def get(self, *a, **k): return _Resp(self._p)

    base = {"id": "1", "login": "nova", "display_name": "Nova",
            "profile_image_url": "x"}

    with patch.object(twitch_oauth.aiohttp, "ClientSession",
                      lambda *a, **k: _Sess({"data": [dict(base, email="A@B.COM")]})):
        u = asyncio.run(twitch_oauth.get_user("tok"))
    assert u["email"] == "a@b.com", "the email is not normalised"

    # The old-grant case: no email key at all.
    with patch.object(twitch_oauth.aiohttp, "ClientSession",
                      lambda *a, **k: _Sess({"data": [base]})):
        u = asyncio.run(twitch_oauth.get_user("tok"))
    assert u["email"] == "", "a token without the scope must not raise"
    assert u["login"] == "nova", "sign-in broke for everyone on an old grant"


# ── storing it ───────────────────────────────────────────────────────────────

def test_an_email_is_stored_with_where_it_came_from(store):
    u = store.upsert_twitch_user("tw1", "nova", "nova")
    store.set_email(u["id"], "  Nova@Example.COM ", source="twitch")
    row = store.get_by_id(u["id"])
    assert row["email"] == "nova@example.com"
    assert row["email_source"] == "twitch"


def test_an_empty_email_never_overwrites_a_real_one(store):
    """Twitch returns nothing for a token without the scope. Writing that
    through would blank an address we already had from Stripe."""
    u = store.upsert_twitch_user("tw1", "nova", "nova")
    store.set_email(u["id"], "paid@example.com", source="stripe")
    store.set_email(u["id"], "", source="twitch")
    assert store.get_by_id(u["id"])["email"] == "paid@example.com"


def test_a_billing_address_beats_a_twitch_account_address(store):
    """A billing email is one somebody typed to receive receipts about money.
    A Twitch account email may be years old and unread. When they differ, keep
    the one that has already been used successfully."""
    u = store.upsert_twitch_user("tw1", "nova", "nova")
    store.set_email(u["id"], "billing@example.com", source="stripe")
    store.set_email(u["id"], "ancient@example.com", source="twitch")
    row = store.get_by_id(u["id"])
    assert row["email"] == "billing@example.com"
    assert row["email_source"] == "stripe"


def test_learning_the_billing_address_later_does_overwrite(store):
    """The other direction is strictly newer information."""
    u = store.upsert_twitch_user("tw1", "nova", "nova")
    store.set_email(u["id"], "twitch@example.com", source="twitch")
    store.set_email(u["id"], "billing@example.com", source="stripe")
    assert store.get_by_id(u["id"])["email"] == "billing@example.com"


def test_the_login_path_stores_the_email_when_there_is_one():
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.twitch_callback)
    assert 'set_email(user["id"], tuser["email"], source="twitch")' in src
    assert 'if tuser.get("email"):' in src, \
        "an absent email would be written through and blank a stored one"


# ── reporting who we cannot reach ────────────────────────────────────────────

def test_the_backfill_asks_twitch_rather_than_assuming():
    """The claim "old tokens cannot return an email" is true, and it is still a
    claim about OAuth. For a given account the honest way to make it is to ask
    — so the script probes each token's real scopes and reports the number."""
    import pathlib
    src = (pathlib.Path(__file__).parent.parent
           / "scripts" / "backfill_emails.py").read_text()
    assert "token_scopes(" in src
    assert '"user:read:email" not in scopes' in src


def test_the_scope_probe_separates_cannot_ask_from_no_scope():
    """None means we could not ask. [] means we asked and the token has
    nothing. Collapsing the two would report a live account as permanently
    unreachable during a Twitch blip, and put it on a list you cannot act on."""
    import asyncio
    from unittest.mock import patch
    from src.auth import twitch_oauth

    assert asyncio.run(twitch_oauth.token_scopes("")) is None, "no token"

    def _boom(*a, **k):
        raise OSError("twitch unreachable")
    with patch.object(twitch_oauth.aiohttp, "ClientSession", _boom):
        assert asyncio.run(twitch_oauth.token_scopes("tok")) is None, "network down"

    class _Resp:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def json(self): return {"scopes": ["clips:edit"]}

    class _Sess:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def get(self, *a, **k): return _Resp()

    with patch.object(twitch_oauth.aiohttp, "ClientSession", lambda *a, **k: _Sess()):
        got = asyncio.run(twitch_oauth.token_scopes("tok"))
    assert got == ["clips:edit"], f"real scopes not reported: {got}"
    assert "user:read:email" not in got, \
        "this is the shape of every pre-cutover token — it has no email scope"


def test_the_backfill_is_read_only_without_apply():
    import pathlib
    src = (pathlib.Path(__file__).parent.parent
           / "scripts" / "backfill_emails.py").read_text()
    assert src.count("set_email(") == 1, \
        "set_email is called outside the --apply branch"
    body = src[src.index("if not args.apply:"):]
    assert "set_email(" in body, "the only write is not behind --apply"


# ── the admin view ───────────────────────────────────────────────────────────

def test_the_panel_can_list_the_people_with_no_email():
    """The working list: everyone here is waiting on a re-login, and there is
    nothing to be done about them until then."""
    from src.dashboard.api import ADMIN_HTML as h
    assert 'data-f="noemail"' in h
    assert "U_FILTER === 'noemail'" in h


def test_the_panel_shows_coverage_and_the_source():
    from src.dashboard.api import ADMIN_HTML as h
    assert "with email" in h, "there is no coverage figure"
    assert "u.email_source" in h, "the drawer does not say where the address came from"
    assert "arrives when they next sign in" in h, \
        "a missing email reads as a bug rather than as a wait"


def test_the_admin_payload_carries_the_source(monkeypatch, tmp_path):
    import base64, json as _j
    from itsdangerous import TimestampSigner
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_clips", {})

    admin = user_store.upsert_twitch_user("tw_a", "boss", "boss", is_admin=True)
    mailed = user_store.upsert_twitch_user("tw_b", "nova", "nova")
    user_store.set_email(mailed["id"], "nova@example.com", source="twitch")
    user_store.upsert_twitch_user("tw_c", "ghost", "ghost")

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": admin["id"], "username": "boss",
         "is_admin": True}).encode())).decode())

    rows = {r["username"]: r for r in c.get("/admin/users").json()}
    assert rows["nova"]["email"] == "nova@example.com"
    assert rows["nova"]["email_source"] == "twitch"
    assert not rows["ghost"].get("email")
    assert rows["ghost"]["email_source"] == "", \
        "an account with no email must not carry a source for one"


# ── the disclosure ───────────────────────────────────────────────────────────

def test_the_privacy_policy_discloses_email_collection():
    """It listed Twitch id, login, display name, avatar, tokens, Kick data and
    billing — and not email, while set_email had been storing billing addresses
    for every paying customer all along. Inaccurate before this change, more so
    after it."""
    from fastapi.testclient import TestClient
    from src.dashboard import api
    text = TestClient(api.app).get("/privacy").text
    assert "Email address" in text, "collection is still undisclosed"
    assert "user:read:email" in text, "the policy does not say it is optional"
    for claim in ("do not sell it", "mailing list", "delete"):
        assert claim in text, f"the policy does not address: {claim}"


def test_deleting_an_account_really_does_delete_the_email(store):
    """The policy now promises this in writing, so it has to be true."""
    u = store.upsert_twitch_user("tw1", "nova", "nova")
    store.set_email(u["id"], "nova@example.com", source="twitch")
    assert store.delete(u["id"]) is True
    assert store.get_by_id(u["id"]) is None
    assert "nova@example.com" not in store._USERS_FILE.read_text()


# ── last seen / last sign-in ─────────────────────────────────────────────────
# Two different facts. Last seen moves on every authenticated request and says
# whether somebody is still using the product. Last sign-in moves only when they
# go through Twitch again, which is what decides whether a newly requested scope
# can reach them — the question the email work leaves open for every existing
# account.

def test_a_login_is_recorded_and_overwrites(store):
    """Not first-touch, unlike checkout_started_at: "did they ever log in" is
    already answered by created_at. What is wanted here is the latest grant."""
    import time
    u = store.upsert_twitch_user("tw1", "nova", "nova")
    assert not store.get_by_id(u["id"]).get("last_login_at")
    store.mark_login(u["id"])
    first = store.get_by_id(u["id"])["last_login_at"]
    assert first > 0
    time.sleep(0.01)
    store.mark_login(u["id"])
    assert store.get_by_id(u["id"])["last_login_at"] > first


def test_marking_a_login_for_a_missing_user_is_not_an_error(store):
    store.mark_login("ghost")


def test_the_oauth_callback_records_the_login():
    import inspect
    from src.dashboard import api
    assert "mark_login(user[\"id\"])" in inspect.getsource(api.twitch_callback)


def test_the_admin_payload_carries_both_clocks(monkeypatch, tmp_path):
    import base64, json as _j, time
    from itsdangerous import TimestampSigner
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_clips", {})

    admin = user_store.upsert_twitch_user("tw_a", "boss", "boss", is_admin=True)
    seen = user_store.upsert_twitch_user("tw_b", "nova", "nova")
    user_store.upsert_twitch_user("tw_c", "ghost", "ghost")
    user_store.mark_login(seen["id"])
    monkeypatch.setattr(api, "_user_last_active", {seen["id"]: time.time() - 300})

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": admin["id"], "username": "boss",
         "is_admin": True}).encode())).decode())

    rows = {r["username"]: r for r in c.get("/admin/users").json()}
    assert rows["nova"]["last_active_at"] > 0
    assert rows["nova"]["last_login_at"] > 0
    # Never-seen accounts report 0 rather than being absent, so the panel can
    # tell "no record" from "undefined" without guessing.
    #
    # Asserted on `ghost`, NOT on the admin: the admin is making this very
    # request, so AuthMiddleware bumps their clock on the way in. An earlier
    # version of this test expected 0 for them and was simply wrong about how
    # the page works.
    assert rows["ghost"]["last_active_at"] == 0
    assert rows["ghost"]["last_login_at"] == 0
    assert rows["boss"]["last_active_at"] > 0, \
        "the admin reading the page is active by definition"


def test_the_panel_renders_last_seen_and_last_sign_in():
    from src.dashboard.api import ADMIN_HTML as h
    assert ">Last seen</th>" in h, "the column is gone"
    assert "u.last_active_at" in h
    assert "<dt>Last sign-in</dt>" in h
    assert "u.last_login_at" in h
    # A blank must read as a gap in our records, not as a claim about the user.
    assert "not since we started counting" in h
    assert "not since we started recording it" in h


def test_the_relative_formatter_is_defined_in_the_block_that_uses_it():
    """THE BUG THIS CAUGHT. The admin page has three separate <script> blocks
    and each defines its own fmt. `ago` went into the wrong one, so the users
    table died with "ago is not defined" — the whole table rendered as
    "Loading…" and no test noticed, because the Python string contained the
    function either way."""
    import re
    from src.dashboard.api import ADMIN_HTML as h
    blocks = re.findall(r"<script>(.*?)</script>", h, re.S)
    users_block = next((b for b in blocks if "function renderUsers" in b), None)
    assert users_block, "renderUsers is not in any script block"
    assert re.search(r"(const|let|function)\s+ago\b", users_block), \
        "ago is not defined in the block that renders the users table"
    assert re.search(r"(const|let|function)\s+fmt\b", users_block), \
        "ago falls back to fmt, which is not defined in this block either"


def test_the_joined_date_survived_the_column_change():
    """Last seen replaced Joined in the header. The join date is still the
    cohort anchor for the referral report, so it stays visible."""
    from src.dashboard.api import ADMIN_HTML as h
    assert "joined ' + fmt(u.created_at)" in h, "the join date is no longer shown"
    assert "<dt>Joined</dt>" in h
