"""Security advisories in the libraries under the app (2026-10-22).

WHY. The owner asked for the site to be secure after the PHP2Shell news.
Highlightz runs no PHP, but auditing every package production runs (the
whole lock, not just requirements.txt, which is what the 10-20 audit looked
at) found 80 advisories in 14 packages. Two in Starlette, the framework
under FastAPI, were reproduced against production's exact package set:

  * CVE-2026-48710. Starlette 1.0.0 rebuilt request.url from the Host header
    without checking it. AuthMiddleware decided on request.url.path, so
    `Host: x/login?` on a signed-out GET /me read as /login at the gate while
    the router still ran /me. Nine pages opened that way (/openapi.json among
    them). Every data endpoint checked the session itself, so nothing private
    leaked, and nginx refuses a Host with a "/" in it, but the gate was wrong.
  * CVE-2026-54283. request.form()'s 1,000-field and 1 MB-per-field limits
    were skipped for urlencoded bodies, so one 4 MB POST to the public
    /login made 1,000,000 fields: 6.8 s holding the one event loop the whole
    site and every stream worker share, and +102 MB. The 10-20 audit noted
    this form DoS as "blocked by Starlette's 1,000-field limit"; that held
    for multipart bodies only.

The fix is both: the gate reads the routed path (request.scope["path"]),
which closes the first on any Starlette, and the lock moves to patched
versions of Starlette and ten more packages (urllib3 was the eleventh and
went back the same day; see FLOORS).
"""

import base64
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from packaging.version import Version
from starlette.datastructures import URL
from starlette.requests import HTTPConnection

from src.dashboard import api

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client():
    return TestClient(api.app)


# ── the gate decides on the path that is served ─────────────────────────────

def test_the_sign_in_gate_reads_the_routed_path_not_the_rebuilt_url():
    src = Path(api.__file__).read_text()
    gate = src[src.index("class AuthMiddleware(BaseHTTPMiddleware):"):src.index("class HeadRequestMiddleware:")]
    assert 'path = request.scope["path"]' in gate
    assert "request.url" not in gate.split('path = request.scope["path"]')[1], \
        "the gate reads request.url again"


def test_a_url_that_lies_about_its_path_does_not_open_a_page(client, monkeypatch):
    """Behaviour, whatever Starlette does with the Host header: make every
    request's URL claim to be the sign-in page, which is open. The gate must
    still see the real path and turn a signed-out request away."""
    monkeypatch.setattr(HTTPConnection, "url", property(lambda self: URL("http://testserver/login")))
    for path in ("/me", "/openapi.json", "/portal", "/demo", "/feedback/unread-count"):
        r = client.get(path, headers={"accept": "application/json"}, follow_redirects=False)
        assert r.status_code == 401, (path, r.status_code, r.text[:80])


@pytest.mark.parametrize("host", ["x/login?", "x/static/a?", "x/health?", "x/auth/x?"])
def test_a_forged_host_header_gets_nothing_past_the_gate(client, host):
    """The request that reproduced it on production's packages."""
    for path in ("/me", "/openapi.json", "/docs", "/portal"):
        r = client.get(path, headers={"host": host}, follow_redirects=False)
        assert r.status_code in (302, 400, 401), (path, host, r.status_code)
        assert '"user_id"' not in r.text


def test_a_signed_in_request_still_gets_through(client, monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: {
        "id": uid, "subscription_status": "active", "plan": "pro", "username": "u1"})
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    data = base64.b64encode(json.dumps({"auth": True, "user_id": "u1",
                                        "subscription_status": "active"}).encode())
    client.cookies.set("session", signer.sign(data).decode())
    r = client.get("/feedback/unread-count")
    assert r.status_code == 200


# ── a form cannot be used to flood the server ───────────────────────────────

def test_a_flood_of_form_fields_is_refused_before_it_is_parsed(client):
    body = "&".join(["f=1"] * 5000)
    r = client.post("/login", content=body,
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 400 and "Too many fields" in r.text


def test_one_huge_form_field_is_refused(client):
    body = "password=" + "x" * (2 * 1024 * 1024)
    r = client.post("/login", content=body,
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 400 and "maximum size" in r.text


def test_an_ordinary_sign_in_form_is_untouched(client):
    r = client.post("/login", data={"username": "nobody", "password": "wrong"},
                    follow_redirects=False)
    assert r.status_code == 302 and "error=" in r.headers["location"]


# ── the lock never drifts back below a patched version ──────────────────────

# Each floor is the first release with the fix for an advisory that applies to
# how this app uses the package. python-jose and ecdsa (no fix exists, nothing
# imports them) and oauthlib (its advisory is for OAuth servers; this app is a
# client) are deliberately absent.
FLOORS = {
    "starlette": "1.3.1",          # forged Host -> request.url; urlencoded form limits
    "python-multipart": "0.0.31",  # quadratic ';' scan, ';' separator, negative Content-Length
    "cryptography": "48.0.1",      # bundled OpenSSL (Kick webhook signatures are verified with it)
    "certifi": "2026.10.19",       # revoked, compromised emSign intermediates
    "aiohttp": "3.14.4",           # client response parser and connection pool fixes
    # NOT urllib3. 2.8.0 calls _PERCENT_RE.sub, and streamlink 8.4.0 replaces
    # _PERCENT_RE with a class that only has subn: every stream URL failed to
    # parse, and every recorder, audio meter and clip fetch died with it
    # (2026-10-22, tests/test_streamlink_urllib3.py). Its advisories are about
    # malicious servers answering a streaming or proxied client; here it
    # talks to Twitch's and Kick's CDNs, AWS and Google. It moves to 2.8.0
    # with streamlink 8.6.2, which works with it, once that is tried live.
    "anyio": "4.14.2",
    "multidict": "6.9.1",
    "httplib2": "0.32.0",          # decompression bomb (YouTube client)
    "pyasn1": "0.6.4",             # quadratic decoding
    "pydantic-settings": "2.14.2",
}


def _lock():
    out = {}
    for line in (ROOT / "requirements.lock").read_text().splitlines():
        body = line.split("#")[0].strip()
        if body:
            name, ver = body.split("==")
            out[re.split(r"\[", name)[0].strip().lower().replace("_", "-")] = ver.strip()
    return out


@pytest.mark.parametrize("name,floor", sorted(FLOORS.items()))
def test_production_runs_at_least_the_patched_version(name, floor):
    lock = _lock()
    assert name in lock, f"{name} left the lock"
    assert Version(lock[name]) >= Version(floor), f"{name} {lock[name]} is below the fix ({floor})"


def test_starlette_is_pinned_directly_not_left_to_fastapis_open_range():
    top = (ROOT / "requirements.txt").read_text()
    assert re.search(r"^starlette==", top, re.M), "starlette is only a transitive pin again"
