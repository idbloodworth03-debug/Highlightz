"""The site audit of 2026-11-24 ("lets run an audit on the site", then "fix all").

What these defend, one per finding:
  1. looking at the site sets no cookie (the landing and sign-in pages used to
     set a session cookie on every visitor); staff are left out of the sign-up
     counts by a cookie set when THEY sign in, and the Cookie Policy lists
     every cookie the site sets (tests/test_funnel.py covers the counting);
  2. a Free account's dashboard asks for nothing its plan refuses (it used to
     GET /uploads, a 403 on every load of Clip Library and the upload screen);
  3. a link to /logout asks before it signs anybody out; only a POST does it,
     and every sign-out button on the site POSTs;
  4. the holiday sale reaches the people who could use it: /me carries it to
     the dashboard and the plans page shows it, and neither offers it to an
     account already paying;
  5. the dashboard is served compiled (scripts/build_dashboard.js) instead of
     downloading Babel and compiling in the browser, and never from a build
     of other JSX.
"""

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.auth import users as user_store
from src.dashboard import api, bundle, holiday_sale as hs
from src.dashboard.aurora_html import DASHBOARD_HTML
from src.dashboard.portal_html import PORTAL_HTML

ROOT = Path(__file__).resolve().parent.parent

PEOPLE = {
    "boss":   {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
    "fan":    {"id": "fan", "username": "fan", "subscription_status": "none"},
    "paying": {"id": "paying", "username": "paying", "subscription_status": "active"},
}


@pytest.fixture(autouse=True)
def world(monkeypatch):
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: dict(PEOPLE[uid]) if uid in PEOPLE else None)
    monkeypatch.setattr(user_store, "_load", lambda: [dict(p) for p in PEOPLE.values()])
    monkeypatch.setattr(user_store, "_save", lambda users: None)


@pytest.fixture
def sale_on(monkeypatch):
    monkeypatch.setattr(api.settings, "holiday_sale_code", "CLIPPER")
    monkeypatch.setattr(api.settings, "holiday_sale_percent", 50)
    monkeypatch.setattr(api.settings, "holiday_sale_last_day", "2099-12-31")
    monkeypatch.setattr(api.settings, "holiday_sale_plans", "pro")


@pytest.fixture
def sale_off(monkeypatch):
    monkeypatch.setattr(api.settings, "holiday_sale_code", "")


def _as(uid=None):
    c = TestClient(api.app, follow_redirects=False, base_url="https://testserver")
    if uid:
        p = PEOPLE[uid]
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": p.get("is_admin", False),
             "subscription_status": p["subscription_status"]}).encode())).decode())
    return c


def _jsx() -> str:
    return bundle.jsx_of(DASHBOARD_HTML)


# ── 1. cookies ───────────────────────────────────────────────────────────────

def test_the_cookie_policy_lists_every_cookie_the_site_sets():
    page = _as().get("/cookies").text
    for name in ("hz_staff", "hz_sale_hidden", "session"):
        assert name in page, f"the Cookie Policy does not mention {name}"
    assert "looking at the site does not set it" in page


def test_a_staff_password_sign_in_marks_the_browser(monkeypatch):
    async def _nothing(ip):
        return None

    async def _account(pw):
        return dict(PEOPLE["boss"]) if pw == "pw" else None
    monkeypatch.setattr(api, "_check_login_rate", _nothing)
    monkeypatch.setattr(api, "_clear_login_rate", _nothing)
    monkeypatch.setattr(api, "_password_account", _account)
    r = _as().post("/login", data={"password": "pw"})
    assert r.status_code == 302
    assert "hz_staff=1" in r.headers.get("set-cookie", "")
    monkeypatch.setitem(PEOPLE, "boss", {**PEOPLE["boss"], "is_admin": False})
    r = _as().post("/login", data={"password": "pw"})
    assert "hz_staff" not in r.headers.get("set-cookie", ""), "a customer's browser was marked as staff"


# ── 2. a Free account asks for nothing its plan refuses ──────────────────────

def test_the_upload_screen_does_not_ask_for_uploads_on_a_plan_without_them():
    jsx = _jsx()
    assert "if(me && me.plan_limits && !me.plan_limits.uploads && !me.is_admin) return;" in jsx


def test_clip_library_reads_edits_only_where_the_editor_is_on():
    jsx = _jsx()
    assert "const edits = useEdits(uploadsOn && editorOn);" in jsx
    assert re.search(r"<LibraryScreen[^>]*editorOn=\{editorOn\}", jsx) or \
        re.search(r"uploadsOn,editorOn,queue", jsx), "the library is not told whether the editor is on"


# ── 3. sign out ──────────────────────────────────────────────────────────────

def test_a_link_to_logout_asks_first_and_signs_nobody_out():
    c = _as("fan")
    r = c.get("/logout")
    assert r.status_code == 200
    assert 'method="post" action="/logout"' in r.text
    assert r.headers.get("cache-control") == "no-store"
    assert "set-cookie" not in r.headers, "the GET touched the session"
    assert c.get("/me").json()["user_id"] == "fan", "the GET signed them out"


def test_logout_for_somebody_signed_out_goes_to_sign_in():
    r = _as().get("/logout")
    assert r.status_code == 302 and r.headers["location"] == "/login"


def test_the_post_signs_out():
    c = _as("fan")
    r = c.post("/logout")
    assert r.status_code == 302 and r.headers["location"] == "/login"
    assert re.search(r"session=null;.*(expires=Thu, 01 Jan 1970|max-age=0)",
                     r.headers.get("set-cookie", ""), re.I), r.headers.get("set-cookie")


def test_every_sign_out_button_posts():
    src = "\n".join(p.read_text() for p in (ROOT / "src").rglob("*.py"))
    assert not re.search(r"""href=["']/logout""", src), "a sign-out link still GETs /logout"
    assert not re.search(r"""location(\.href)?\s*=\s*['"]/logout""", src)
    assert '<form method="post" action="/logout"' in PORTAL_HTML


# ── 4. the sale in the dashboard and on the plans page ───────────────────────

def test_me_carries_the_sale_to_whoever_could_use_it(sale_on):
    fan = _as("fan").get("/me").json()["sale"]
    assert fan and fan["code"] == "CLIPPER" and fan["pct"] == 50
    assert [p["key"] for p in fan["plans"]] == ["pro"]
    assert fan["ends_ms"] > 0
    assert _as("boss").get("/me").json()["sale"], "staff should see it, to check it"
    assert _as("paying").get("/me").json()["sale"] is None, "offered to an account already paying"


def test_no_sale_no_sale_anywhere(sale_off):
    assert _as("fan").get("/me").json()["sale"] is None
    page = _as("fan").get("/billing/paywall").text
    assert 'class="sale-top"' not in page and "SALE_PAYWALL" not in page


def test_the_plans_page_shows_the_sale(sale_on):
    page = _as("fan").get("/billing/paywall").text
    assert 'class="sale-top"' in page and "CLIPPER" in page
    assert 'class="sale-note"' in page
    assert "SALE_PAYWALL" not in page, "a placeholder was left on the page"
    paying = _as("paying").get("/billing/paywall").text
    assert 'class="sale-top"' not in paying and "SALE_PAYWALL" not in paying


def test_the_dashboard_shows_it_where_people_decide():
    jsx = _jsx()
    assert "function SaleStrip(" in jsx and "function SaleLine(" in jsx
    assert "<SaleStrip me={me}/>" in jsx
    assert jsx.count("<SaleLine me={me}") >= 5, "the line under Upgrade to Pro went missing"
    # It ends by itself in an open tab: hidden once ends_ms passes.
    assert "ends_ms" in jsx
    # The dismissal is per code, so next year's sale shows again.
    assert "hz_sale_hidden" in jsx


def test_the_sale_reaches_an_open_tab_after_a_reconnect():
    """CLAUDE.md realtime rule 3: the sale comes on /me, and /me is re-read by
    refetchAll() on every reconnect (a deploy restarts the server)."""
    jsx = _jsx()
    i = jsx.index("const refetchAll = useCallback(")
    body = jsx[i:jsx.index("},[", i)]
    assert "'/me'" in body or '"/me"' in body, "refetchAll() no longer re-reads /me"


def test_for_app_turns_paying_accounts_away(sale_on):
    assert hs.for_app(None), "a visitor on the plans page is somebody new"
    for status in ("active", "past_due", "incomplete"):
        assert hs.for_app({"subscription_status": status}) is None, status
    for status in ("none", "expired", "canceled", "trialing"):
        assert hs.for_app({"subscription_status": status}), status


# ── 5. the dashboard served compiled ─────────────────────────────────────────

def test_the_built_dashboard_is_built_from_the_jsx_in_the_source():
    """If this fails, the JSX changed and nobody rebuilt: run
    `node scripts/build_dashboard.js` and commit src/dashboard/static/dashboard.js."""
    assert bundle.BUNDLE.exists(), "src/dashboard/static/dashboard.js is missing"
    assert bundle.bundle_hash() == bundle.source_hash(), \
        "dashboard.js is stale: run node scripts/build_dashboard.js"


def test_the_served_dashboard_does_not_compile_in_the_browser():
    html = bundle.SERVED_HTML
    assert '<script type="text/babel">' not in html
    assert "@babel/standalone" not in html
    assert f'/static/dashboard.js?v={bundle.source_hash()[:12]}' in html
    assert len(html) < len(DASHBOARD_HTML) / 2


def test_the_dashboard_route_serves_the_compiled_page():
    r = _as("fan").get("/")
    assert r.status_code == 200
    assert "/static/dashboard.js?v=" in r.text
    js = _as().get("/static/dashboard.js")
    assert js.status_code == 200 and "source-sha256:" in js.text[:600]


def test_a_stale_build_is_never_served(tmp_path):
    stale = tmp_path / "dashboard.js"
    stale.write_text("/* source-sha256: " + "0" * 64 + " */\nvar x;\n")
    assert bundle.served(DASHBOARD_HTML, stale) == DASHBOARD_HTML
    assert bundle.served(DASHBOARD_HTML, tmp_path / "missing.js") == DASHBOARD_HTML


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_the_build_script_agrees():
    if not (ROOT / "node_modules" / "@babel" / "standalone").exists():
        pytest.skip("npm install has not been run")
    r = subprocess.run(["node", "scripts/build_dashboard.js", "--check"], cwd=ROOT,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
