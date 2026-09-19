"""Meta's two required callbacks, and the signature that is their only lock.

"Instagram API with Instagram Login" will not let an app be configured
without a Deauthorize callback and a Data Deletion Request URL. Both are
called server-to-server with no session, so both are in `_OPEN_PATHS` — the
same lesson as TikTok's domain-verification file, where a redirect to /login
read to the verifier as "this endpoint does not work".

THE SIGNATURE IS THE WHOLE SECURITY MODEL. Each request is a `signed_request`
carrying an Instagram user id and an HMAC-SHA256 of the encoded payload under
the app secret. Without the check, an open endpoint that disconnects an
account by its Instagram id would let anybody on the internet disconnect any
connected account — Instagram ids are public. So an unverifiable request must
touch nothing.
"""

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api
from src.publish import connections as pc

SECRET = "app-secret-for-tests"


def signed(payload: dict, secret: str = SECRET) -> str:
    pj = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    sig = hmac.new(secret.encode(), pj.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(sig).rstrip(b"=").decode() + "." + pj


@pytest.fixture
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(api.settings, "instagram_app_secret", SECRET)
    monkeypatch.setattr(pc, "_INDEX", tmp_path / "publish_connections.json")
    pc._conns.clear(); pc._loaded = True
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)

    def connect(uid="u1", account_id="1784123"):
        return pc.save(pc.Connection(user_id=uid, platform="instagram",
                                     account_id=account_id, account_name="@nova",
                                     access_token="tok-secret"))

    yield type("S", (), {"connect": staticmethod(connect), "sent": sent,
                         "client": TestClient(api.app)})
    pc._conns.clear(); pc._loaded = False


# ── deauthorize ──────────────────────────────────────────────────────────────

def test_removing_the_app_on_instagram_drops_the_connection(scene):
    scene.connect()
    r = scene.client.post("/instagram/deauthorize",
                          data={"signed_request": signed({"user_id": "1784123"})})
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert pc.get("u1", "instagram") is None, "a dead token was kept"


def test_the_users_tab_is_told_live(scene):
    """Realtime contract: the Scheduler's account row goes back to Connect
    without a refresh."""
    scene.connect()
    scene.client.post("/instagram/deauthorize",
                      data={"signed_request": signed({"user_id": "1784123"})})
    assert ("publish_connections_changed", "u1") in scene.sent


def test_only_that_account_is_touched(scene):
    scene.connect(uid="u1", account_id="111")
    scene.connect(uid="u2", account_id="222")
    scene.client.post("/instagram/deauthorize",
                      data={"signed_request": signed({"user_id": "111"})})
    assert pc.get("u1", "instagram") is None
    assert pc.get("u2", "instagram") is not None


def test_an_account_we_never_had_is_still_a_success(scene):
    """"We hold nothing of theirs" is the state Meta is asking us to reach,
    and answering 404 would have them retry forever."""
    r = scene.client.post("/instagram/deauthorize",
                          data={"signed_request": signed({"user_id": "nobody"})})
    assert r.status_code == 200


# ── the lock ─────────────────────────────────────────────────────────────────

def test_a_forged_signature_disconnects_nothing(scene):
    scene.connect()
    r = scene.client.post("/instagram/deauthorize",
                          data={"signed_request": signed({"user_id": "1784123"}, "wrong-secret")})
    assert r.status_code == 400
    assert pc.get("u1", "instagram") is not None, "anybody could disconnect any account"


@pytest.mark.parametrize("raw", ["", "garbage", "onlyonepart",
                                 "!!!.e30", "e30.!!!", "."])
def test_malformed_requests_are_refused_not_crashed(scene, raw):
    scene.connect()
    r = scene.client.post("/instagram/deauthorize", data={"signed_request": raw})
    assert r.status_code == 400
    assert pc.get("u1", "instagram") is not None


def test_a_payload_that_is_not_an_object_is_refused(scene):
    scene.connect()
    r = scene.client.post("/instagram/deauthorize",
                          data={"signed_request": signed(["not", "a", "dict"])})
    assert r.status_code == 400


def test_with_no_app_secret_configured_nothing_verifies(scene, monkeypatch):
    """An unconfigured app must fail closed. Verifying against an empty
    secret would accept a signature anybody could compute."""
    monkeypatch.setattr(api.settings, "instagram_app_secret", "")
    scene.connect()
    r = scene.client.post("/instagram/deauthorize",
                          data={"signed_request": signed({"user_id": "1784123"}, "")})
    assert r.status_code == 400
    assert pc.get("u1", "instagram") is not None


def test_the_helper_lets_a_coding_error_through_rather_than_hiding_it():
    """A bare `except Exception` hid a missing `import base64` in development:
    every signature came back invalid, which is indistinguishable from a bad
    secret and would have been a long hunt on a live app."""
    import inspect
    src = inspect.getsource(api._meta_signed_request)
    assert "except (ValueError, TypeError):" in src
    # Code lines only. The comment above that `except` explains the bug by
    # naming `except Exception`, and a naive substring check matches its own
    # documentation — which is how a guard ends up passing for the wrong
    # reason, or failing for one.
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    assert "except Exception" not in code


# ── data deletion ────────────────────────────────────────────────────────────

def test_a_deletion_request_deletes_and_returns_meta_s_receipt(scene):
    scene.connect()
    r = scene.client.post("/instagram/data-deletion",
                          data={"signed_request": signed({"user_id": "1784123"})})
    assert r.status_code == 200
    body = r.json()
    assert body["confirmation_code"]
    assert body["url"].startswith("https://") and body["confirmation_code"] in body["url"]
    assert pc.get("u1", "instagram") is None


def test_the_confirmation_code_is_stable_for_the_same_account(scene):
    """Meta may ask twice. Two different codes for one request would read as
    two different deletions."""
    scene.connect()
    a = scene.client.post("/instagram/data-deletion",
                          data={"signed_request": signed({"user_id": "1784123"})}).json()
    scene.connect()
    b = scene.client.post("/instagram/data-deletion",
                          data={"signed_request": signed({"user_id": "1784123"})}).json()
    assert a["confirmation_code"] == b["confirmation_code"]


def test_the_status_page_renders_for_the_person_meta_sends(scene):
    r = scene.client.get("/instagram/data-deletion/status?code=abc123def")
    assert r.status_code == 200
    assert "abc123def" in r.text
    assert "deleted" in r.text.lower()


def test_the_status_page_cannot_be_used_to_inject_markup(scene):
    r = scene.client.get("/instagram/data-deletion/status?code=<script>x</script>")
    assert r.status_code == 200
    assert "<script>x</script>" not in r.text


# ── reachable at all ─────────────────────────────────────────────────────────

def test_all_three_are_open_paths():
    """Meta calls with no session. A redirect to /login is, to a machine,
    an endpoint that does not work — exactly how TikTok's verifier read one."""
    for p in ("/instagram/deauthorize", "/instagram/data-deletion",
              "/instagram/data-deletion/status"):
        assert p in api._OPEN_PATHS, f"{p} sits behind the login redirect"
