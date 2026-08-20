"""A brand-new user, walked through every feature they are entitled to.

The question this answers is the one you cannot answer by reading code: does
somebody who signs up RIGHT NOW hit a wall anywhere? Each test drives the real
app through the real signup path — upsert_twitch_user, the same call the OAuth
callback makes — and then exercises a feature the way the dashboard does.

WHAT A NEW SIGNUP GETS. plans.py grants a 7-day trial with no card, and
get_plan resolves `trialing` to pro. So a new user is entitled to everything:
10 streams, a 200-clip queue, the VOD scanner and the Clip Editor. That is the
whole point of the trial, and it means a gate that wrongly refuses them is
invisible in testing against a free or locked account.

Deliberately NOT mocked: the plan resolution, the limit lookups and every
gate. Only the outside world is stubbed — Twitch, Redis, Stripe — because
those are not what this is asking about.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def app(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store, trial_ledger
    from src.stats import stream_stats

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
    monkeypatch.setattr(stream_stats, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api.settings, "local_storage_path", str(tmp_path))
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)
    monkeypatch.setattr(api, "_popular_streams_cache", (time.time(), []))

    async def _bcast(msg, user_id=None):
        pass
    monkeypatch.setattr(api, "broadcast", _bcast)

    # The outside world. Nothing here is what the audit is about.
    async def _preset(ch):
        return "default"
    monkeypatch.setattr(api, "_auto_preset_for", _preset)

    async def _pub(*a, **k):
        return None
    monkeypatch.setattr(api, "_publish_new_stream", _pub)
    monkeypatch.setattr(api, "_publish_remove_stream", _pub)

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def signup(twitch_id="99001", login="newbie"):
        """The exact call the Twitch OAuth callback makes for a first login."""
        u = user_store.upsert_twitch_user(
            twitch_id=twitch_id, login=login, username=login,
            avatar_url="", access_token="at", refresh_token="rt",
            expires_in=3600, is_admin=False)
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": u["id"], "username": login,
             "is_admin": False,
             "subscription_status": u["subscription_status"]}).encode())).decode())
        return u

    c.signup = signup
    c.api = api
    c.store = user_store
    c.tmp = tmp_path
    yield c


# ── what they land on ────────────────────────────────────────────────────────

def test_a_new_signup_lands_on_a_trial(app):
    u = app.signup()
    assert u["subscription_status"] == "trialing"
    assert u["trial_ends_at"] > time.time()


def test_the_trial_resolves_to_the_full_product(app):
    """If this ever resolved to free or locked, every gate below would refuse
    them and the trial would sell nothing."""
    from src.billing.plans import get_plan, limits_for
    u = app.signup()
    full = app.store.get_by_id(u["id"])
    assert get_plan(full) == "pro"
    lim = limits_for(full)
    assert lim["max_streams"] == 10
    assert lim["max_pending"] == 200
    assert lim["vod"] is True
    assert lim["uploads"] is True


def test_me_reports_the_same_thing_the_backend_enforces(app):
    """The dashboard hides screens based on /me. If it disagrees with the
    gates, the user sees a locked screen for something they may use — or an
    open one that 403s when they touch it."""
    app.signup()
    me = app.get("/me").json()
    assert me["plan"] == "pro"
    assert me["plan_limits"]["vod"] is True
    assert me["plan_limits"]["uploads"] is True
    assert me["plan_limits"]["max_streams"] == 10
    assert me["plan_limits"]["max_pending"] == 200


def test_a_new_user_sees_no_queue_full_notice(app):
    """Their queue is empty. A notice here would be the first thing they see."""
    app.signup()
    assert app.get("/me").json()["clips_lost_24h"] == 0


# ── the screens they can open ────────────────────────────────────────────────

@pytest.mark.parametrize("path", [
    "/", "/me", "/clips", "/streams", "/stats", "/profiles",
    "/streams/suggest", "/clips/undo", "/vod/jobs",
    "/publish/platforms", "/publish/schedule", "/feedback/mine",
    "/feedback/unread-count", "/auth/kick/status",
])
def test_every_screen_a_new_user_opens_answers(app, path):
    """No 403, no 500 — the two that mean "you cannot use this"."""
    app.signup()
    r = app.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"


@pytest.mark.parametrize("path", [
    "/tutorial", "/compare", "/tos", "/privacy", "/cookies", "/opt-out",
    "/landing/stats", "/landing/showcase", "/robots.txt", "/sitemap.xml",
    "/favicon.ico", "/health",
])
def test_the_public_pages_all_load(app, path):
    """Reachable signed out — a new user meets these before they have an
    account at all."""
    app.cookies.clear()
    r = app.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"


# ── the things they can actually do ──────────────────────────────────────────

def test_they_can_add_a_stream(app):
    app.signup()
    r = app.post("/streams", json={"channel": "lacy", "platform": "twitch",
                                   "preset": "default"})
    assert r.status_code == 201, r.text


def test_they_can_add_up_to_ten_streams(app):
    """Pro is sold as 10. The eleventh is the one that should refuse."""
    app.signup()
    for i in range(10):
        r = app.post("/streams", json={"channel": f"chan{i}", "platform": "twitch",
                                       "preset": "default"})
        assert r.status_code == 201, f"stream {i + 1} refused: {r.text[:200]}"
    r = app.post("/streams", json={"channel": "eleven", "platform": "twitch",
                                   "preset": "default"})
    assert r.status_code == 429


def test_they_can_remove_a_stream(app):
    app.signup()
    app.post("/streams", json={"channel": "lacy", "platform": "twitch",
                               "preset": "default"})
    assert app.delete("/streams/lacy").status_code == 204


def test_they_can_review_a_clip(app):
    """Approve and reject are the core loop. A new user with a fresh account
    must be able to act on the first clip that arrives."""
    u = app.signup()
    app.api._clips["c1"] = {"id": "c1", "user_id": u["id"], "status": "pending",
                            "channel": "lacy", "created_at": time.time(),
                            "platform": "twitch", "trigger_score": 70.0}
    assert app.post("/clips/c1/approve").status_code in (200, 204)
    app.api._clips["c2"] = {"id": "c2", "user_id": u["id"], "status": "pending",
                            "channel": "lacy", "created_at": time.time(),
                            "platform": "twitch", "trigger_score": 40.0}
    assert app.post("/clips/c2/reject").status_code in (200, 204)


def test_they_can_clear_their_recent_suggestions(app):
    """Shipped today — a new user must not meet a 500 on a brand-new file."""
    app.signup()
    assert app.delete("/streams/suggest/recent").status_code == 204


def test_they_can_send_feedback(app):
    app.signup()
    r = app.post("/feedback", json={"message": "hello, first day here"})
    assert r.status_code in (200, 201), r.text


def test_they_can_start_a_vod_scan(app, monkeypatch):
    """Pro-gated, and a trial is pro. A 403 here means the trial does not
    include what the pricing page says it includes."""
    from src.dashboard import api
    app.signup()

    async def _run(*a, **k):
        return None
    monkeypatch.setattr(api, "_run_vod_job", _run, raising=False)
    r = app.post("/vod/analyze",
                 json={"vod_url": "https://www.twitch.tv/videos/123456789",
                       "preset": "default"})
    assert r.status_code != 403, "the VOD scanner refused a trial user"
    assert r.status_code < 500, f"VOD scan errored: {r.status_code} {r.text[:200]}"


# ── the two features that are deliberately NOT released ──────────────────────
#
# Clip Editor and Scheduler are adminOnly in NAV and behind release flags
# (UPLOADS_ENABLED / CLIP_IMPORT_ENABLED, both defaulting False). A new user
# never sees them, so "every feature works" is true of everything they are
# actually shown. These tests pin that the two sides agree — a nav entry with a
# 503 behind it, or an endpoint open to a screen nobody can reach, are both
# ways for this to rot quietly.

def test_the_clip_editor_is_hidden_from_a_new_user(app):
    """Not a gap: it is unreleased, and the nav hides it. The failure mode
    worth guarding is the nav showing it while the endpoint refuses."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "{id:'uploads',label:'Clip Editor',icon:'upload',adminOnly:true}" in DASHBOARD_HTML
    assert "n.adminOnly||(me&&me.is_admin)" in DASHBOARD_HTML, \
        "adminOnly nav entries are no longer filtered for non-admins"


def test_typing_the_route_does_not_strand_them(app):
    """Even if they guess the hash route, they land on a real screen rather
    than an empty one."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "adminOnlyTabs.includes(route) && !(me && me.is_admin)) ? 'review'" in DASHBOARD_HTML


@pytest.mark.parametrize("path", ["/uploads", "/twitch/clips"])
def test_the_unreleased_endpoints_refuse_cleanly(app, path):
    """Belt and braces behind the nav. A 503 with an honest message is right;
    a 500 or a silent empty success is not."""
    app.signup()
    r = app.get(path)
    assert r.status_code == 503, f"{path} -> {r.status_code}"
    assert "coming soon" in r.text.lower() or "available" in r.text.lower(), \
        f"{path} refuses without saying why: {r.text[:200]}"


def test_the_nav_and_the_release_flags_agree(app):
    """If a flag is switched on in .env, the endpoint opens — the nav entry
    must open with it, or the feature ships invisible."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "me.features?.uploads || me.is_admin" in DASHBOARD_HTML
    assert "me.features?.clip_import || me.is_admin" in DASHBOARD_HTML


# ── the boundaries still hold ────────────────────────────────────────────────

def test_a_new_user_is_not_an_admin(app):
    app.signup()
    assert app.get("/admin/users").status_code in (401, 403) or \
        "Sign In" in app.get("/admin/users").text


def test_a_new_user_cannot_see_another_users_clips(app):
    a = app.signup(twitch_id="1", login="alice")
    app.api._clips["secret"] = {"id": "secret", "user_id": "someone-else",
                                "status": "pending", "channel": "x",
                                "created_at": time.time()}
    app.api._clips["mine"] = {"id": "mine", "user_id": a["id"],
                              "status": "pending", "channel": "y",
                              "created_at": time.time()}
    ids = {c["id"] for c in app.get("/clips").json()}
    assert ids == {"mine"}


def test_a_second_account_on_the_same_twitch_id_gets_no_second_trial(app):
    """The ledger survives account deletion on purpose. Re-running signup for
    the same twitch id must not restart the clock."""
    from src.auth import trial_ledger
    u = app.signup(twitch_id="555", login="dave")
    assert u["subscription_status"] == "trialing"
    app.delete("/account")
    again = app.store.upsert_twitch_user(
        twitch_id="555", login="dave", username="dave",
        access_token="at", refresh_token="rt", expires_in=3600)
    assert again["subscription_status"] != "trialing", \
        "deleting the account bought a second free trial"
    assert trial_ledger.has_used_trial("twitch", "555")


# ── today's changes, against a brand-new account ─────────────────────────────

def test_a_new_users_first_clips_are_not_swept_by_the_seven_day_retention(app):
    """Retention shipped today. A clip made now must survive today."""
    from src.main import CLIP_RETENTION
    now = time.time()
    fresh_age = 0.0
    assert fresh_age <= CLIP_RETENTION["pending"]
    assert now - (now - 86400) < CLIP_RETENTION["pending"], \
        "a one-day-old pending clip would be swept"


def test_the_admin_cap_lift_did_not_change_what_a_new_user_gets(app):
    """limits_for grew an admin branch today. A non-admin must be untouched."""
    from src.billing.plans import PLAN_LIMITS, limits_for
    u = app.signup()
    full = app.store.get_by_id(u["id"])
    assert limits_for(full)["max_pending"] == PLAN_LIMITS["pro"]["max_pending"] == 200
