"""Released with TikTok alone (owner, 2026-10-05).

"okay we have been approved for TikTok lets get this rolled out ASAP" — the
Clip Editor, Scheduler and Autopilot open to Pro, with TikTok the only posting
platform; "keep youtube and instagram to coming soon" (Meta's review is not
done, so Instagram refuses every non-tester; Google has not verified the app).

`PUBLIC_PLATFORMS` (default "tiktok", src/publish/release.py) decides which
platforms an ordinary account may connect. Admins and early-access accounts
keep all of them, so testing and app review still work.

What these defend:
  1. the release module's arithmetic;
  2. an ordinary account sees YouTube/Instagram as "soon", cannot start their
     OAuth, and cannot Post now to them; admins and early access can;
  3. no public page promises YouTube/Instagram posting or "auto-posting"
     while only TikTok is open — and they still do once everything is;
  4. TikTok's "may land as private until reviewed" note is gone once audited;
  5. the Autopilot hint does not send an ordinary account to a "soon" chip.
"""

import base64
import inspect
import json
import os
import subprocess
import sys

import pytest
from itsdangerous import TimestampSigner

from src.auth import users as user_store
from src.publish import release


# ── 1. release arithmetic ────────────────────────────────────────────────────

def test_the_default_is_tiktok_only(monkeypatch):
    from config.settings import Settings
    assert Settings.model_fields["public_platforms"].default == "tiktok"
    monkeypatch.setattr(release.settings, "uploads_enabled", True)
    monkeypatch.setattr(release.settings, "public_platforms", "tiktok")
    assert release.live_platforms() == ("tiktok",)
    assert release.held_platforms() == ("youtube", "instagram")
    assert not release.auto_posting_live()


def test_nothing_is_live_while_uploads_are_held(monkeypatch):
    monkeypatch.setattr(release.settings, "uploads_enabled", False)
    monkeypatch.setattr(release.settings, "public_platforms", "tiktok,instagram")
    assert release.live_platforms() == ()
    assert release.public_platforms() == ("tiktok", "instagram")


def test_adding_instagram_turns_auto_posting_on(monkeypatch):
    monkeypatch.setattr(release.settings, "uploads_enabled", True)
    monkeypatch.setattr(release.settings, "public_platforms", " TikTok , instagram,bogus")
    assert release.live_platforms() == ("tiktok", "instagram")
    assert release.auto_posting_live()
    assert release.join(release.held_platforms()) == "YouTube"


def test_admins_and_early_access_see_everything(monkeypatch):
    monkeypatch.setattr(release.settings, "public_platforms", "tiktok")
    assert release.open_for("instagram", {"is_admin": True})
    assert release.open_for("youtube", {"early_access": True})
    assert not release.open_for("instagram", {"id": "x"})
    assert not release.open_for("instagram", None)
    assert release.open_for("tiktok", {"id": "x"})


def test_join_reads_like_english():
    assert release.join(()) == ""
    assert release.join(("tiktok",)) == "TikTok"
    assert release.join(("youtube", "tiktok", "instagram")) == "YouTube, TikTok and Instagram"


# ── 2. the API ───────────────────────────────────────────────────────────────

PEOPLE = {}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.publish import connections
    PEOPLE.clear()
    PEOPLE.update({
        "pro":   {"id": "pro", "username": "pro", "plan": "pro", "subscription_status": "active"},
        "boss":  {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
        "early": {"id": "early", "username": "early", "plan": "pro", "subscription_status": "active",
                  "early_access": True},
    })
    monkeypatch.setattr(user_store, "_load", lambda: list(PEOPLE.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(connections, "_INDEX", tmp_path / "conns.json")
    connections._conns.clear(); connections._loaded = False
    s = api.settings
    for k, v in (("uploads_enabled", True), ("public_platforms", "tiktok"),
                 ("tiktok_client_key", "k"), ("tiktok_client_secret", "s"),
                 ("instagram_app_id", "i"), ("instagram_app_secret", "s"),
                 ("google_client_id", "g"), ("google_client_secret", "s")):
        monkeypatch.setattr(s, k, v)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(s.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        p = PEOPLE[uid]
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": p.get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c
    c.login = login
    return c


def _rows(c):
    return {r["id"]: r for r in c.get("/publish/connections").json()["platforms"]}


def test_an_ordinary_account_sees_youtube_and_instagram_as_soon(client):
    rows = _rows(client.login("pro"))
    assert rows["tiktok"]["configured"] and not rows["tiktok"]["held"]
    for p in ("youtube", "instagram"):
        assert rows[p]["configured"] is False and rows[p]["held"] is True


@pytest.mark.parametrize("who", ["boss", "early"])
def test_admins_and_early_access_can_connect_everything(client, who):
    rows = _rows(client.login(who))
    assert all(r["configured"] and not r["held"] for r in rows.values())
    r = client.get("/publish/connect/instagram", follow_redirects=False)
    assert r.status_code in (302, 307)


def test_an_ordinary_account_cannot_start_a_held_connection(client):
    c = client.login("pro")
    for p, label in (("instagram", "Instagram"), ("youtube", "YouTube")):
        r = c.get(f"/publish/connect/{p}", follow_redirects=False)
        assert r.status_code == 403 and r.json()["detail"] == f"Posting to {label} is coming soon."
    r = c.get("/publish/connect/tiktok", follow_redirects=False)
    assert r.status_code in (302, 307) and "tiktok.com" in r.headers["location"]


def test_post_now_refuses_a_held_platform_by_name(client):
    r = client.login("pro").post("/publish/post-now",
                                 json={"clip_id": "nope", "platforms": ["tiktok", "instagram"]})
    assert r.status_code == 403 and "Instagram" in r.json()["detail"]


# ── 3/4. public copy, as built at import with the production switches ────────

_SNIP = r"""
import asyncio, re
from src.dashboard import api, compare_content as C
def text(h): return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", h))
print("LANDING::" + text(api.LANDING_HTML))
print("RAW::" + api.LANDING_HTML.replace("\n", " "))
def body(r): return (r.body.decode() if hasattr(r, "body") else str(r)).replace("\n", " ")
print("LLMS::" + body(asyncio.run(api.llms_txt())))
print("FULL::" + body(asyncio.run(api.llms_full_txt())))
print("PAYWALL::" + api._paywall_copy("new")["subline"])
row = next(f for f in C.FEATURES if f[0] == "Auto-posts to Shorts and Reels")
print("ROW::" + repr(row[1]) + "|" + row[4])
"""


def _surfaces(platforms: str) -> dict:
    env = dict(os.environ, UPLOADS_ENABLED="true", PUBLIC_PLATFORMS=platforms,
               TIKTOK_AUDITED="true")
    out = subprocess.run([sys.executable, "-c", _SNIP], env=env,
                         capture_output=True, text=True, timeout=180)
    assert out.returncode == 0, out.stderr[-2000:]
    got = {}
    for line in out.stdout.splitlines():
        k, _, v = line.partition("::")
        if k in ("LANDING", "RAW", "LLMS", "FULL", "PAYWALL", "ROW"):
            got[k] = v
    return got


@pytest.fixture(scope="module")
def tiktok_only():
    return _surfaces("tiktok")


def test_no_page_promises_youtube_or_instagram_posting(tiktok_only):
    s = tiktok_only
    for name in ("LANDING", "RAW", "LLMS", "FULL", "PAYWALL"):
        low = s[name].lower()
        for bad in ("auto-posting built in", "posts it for you",
                    "posted to youtube, tiktok and instagram",
                    "posts to youtube, tiktok and instagram",
                    "posts them to youtube, tiktok and instagram",
                    "post it to youtube, tiktok and instagram",
                    "connect your youtube and instagram accounts once and the scheduler posts"):
            assert bad not in low, f"{name} still says {bad!r}"


def test_the_pages_say_what_is_open(tiktok_only):
    s = tiktok_only
    assert "posting to TikTok built in" in s["RAW"]
    assert "Posting to YouTube and Instagram is coming soon" in s["LANDING"]
    assert "posted to TikTok from the Scheduler" in s["LLMS"]
    assert "Posting to YouTube and Instagram is coming soon." in s["LLMS"]
    assert "the Scheduler to post it to TikTok" in s["PAYWALL"]
    assert s["ROW"].startswith("'Soon'|")


def test_the_tiktok_review_caveat_is_gone_once_audited(tiktok_only):
    assert "until TikTok finishes reviewing" not in tiktok_only["LANDING"]


def test_everything_returns_when_every_platform_opens():
    s = _surfaces("youtube,tiktok,instagram")
    assert "auto-posting built in" in s["RAW"]
    assert "coming soon" not in s["PAYWALL"]
    assert "YouTube, TikTok and Instagram" in s["LLMS"]
    assert s["ROW"].startswith("True|")


# ── 5. the Autopilot hint ────────────────────────────────────────────────────

def test_autopilot_does_not_point_at_a_soon_chip():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert "const autoHeld = (connections||[]).length > 0 && !(connections||[]).some(c=>c.id !== 'tiktok' && c.configured);" in h
    assert "Automatic posting to YouTube and Instagram is coming soon." in h


def test_the_landing_page_sells_autopilot_now_it_is_open(tiktok_only):
    """Owner, 2026-10-05: "make sure the landing page has the up to date
    things too". Autopilot opened to Pro with the Scheduler but the landing
    page never named it."""
    t = tiktok_only["LANDING"]
    assert "Scheduler Yes Autopilot Yes Get Pro" in t
    assert "Switch on Autopilot and every clip you approve is reframed" in t
    assert "Autopilot, also Pro" in t


def test_autopilot_is_soon_on_the_plan_table_while_held():
    from src.dashboard import api
    src = inspect.getsource(api)
    assert '("Autopilot", _released("uploads", limits)),' in src
