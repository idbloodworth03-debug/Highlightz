"""Admins have no pending-clip cap.

The cap is read from limits_for() by every path that enforces it, so the lift
belongs there and nowhere else. What these tests mostly guard is the blast
radius: that lifting it for admins did not lift it for anybody else, and did
not corrupt the shared PLAN_LIMITS dict on the way past.

THE SHARED-DICT TRAP. PLAN_LIMITS holds ONE dict per plan. Building the
override by mutating the value in place — limits["max_pending"] = BIG — would
rewrite the cap for every pro user on the box, permanently, with no diff to
show for it and no error at the time. limits_for() therefore returns a copy,
and test_the_shared_plan_table_is_not_mutated is the test that would have
caught the other version.
"""

import pytest

from src.billing.plans import (PLAN_LIMITS, UNLIMITED_PENDING, get_plan,
                               limits_for)


def _admin(**extra):
    return {"id": "a1", "is_admin": True, "subscription_status": "none", **extra}


def _pro(**extra):
    return {"id": "u1", "subscription_status": "active", "plan": "pro", **extra}


# ── the feature ──────────────────────────────────────────────────────────────

def test_an_admin_has_no_pending_cap():
    assert limits_for(_admin())["max_pending"] == UNLIMITED_PENDING


def test_the_admin_cap_is_far_above_any_real_queue():
    """It has to be a number nobody reaches, not merely a bigger one."""
    assert limits_for(_admin())["max_pending"] > 1_000_000
    assert limits_for(_admin())["max_pending"] > PLAN_LIMITS["pro"]["max_pending"] * 1000


def test_an_admin_keeps_every_other_pro_entitlement():
    """Only the clip cap moves. Streams are a real resource guard — each one
    holds a chat socket, a streamlink and an ffmpeg on one vCPU — so lifting
    that is a different decision and was not asked for."""
    lim = limits_for(_admin())
    assert lim["max_streams"] == PLAN_LIMITS["pro"]["max_streams"]
    assert lim["vod"] is True
    assert lim["uploads"] is True
    assert lim["label"] == PLAN_LIMITS["pro"]["label"]


def test_an_admin_with_no_subscription_still_has_no_cap():
    """Admins resolve to pro without paying, so the lift must not quietly
    depend on a subscription being present."""
    assert limits_for(_admin(subscription_status="expired"))["max_pending"] \
        == UNLIMITED_PENDING


# ── the blast radius ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("plan,user", [
    ("pro",     _pro()),
    ("starter", {"id": "u2", "subscription_status": "active", "plan": "starter"}),
    ("free",    {"id": "u3", "subscription_status": "none", "grandfathered": True}),
    ("locked",  {"id": "u4", "subscription_status": "none"}),
])
def test_non_admins_keep_exactly_the_cap_they_had(plan, user):
    assert get_plan(user) == plan
    assert limits_for(user)["max_pending"] == PLAN_LIMITS[plan]["max_pending"]


def test_the_shared_plan_table_is_not_mutated():
    """THE one the in-place version would have failed. Resolving an admin must
    leave the pro row untouched for everyone else."""
    before = PLAN_LIMITS["pro"]["max_pending"]
    limits_for(_admin())
    limits_for(_admin())
    assert PLAN_LIMITS["pro"]["max_pending"] == before == 200
    assert limits_for(_pro())["max_pending"] == 200


def test_mutating_a_returned_limits_dict_cannot_poison_the_table():
    """Callers get a dict; nothing stops one from writing to it. The admin path
    hands back a copy, so a stray write cannot reach PLAN_LIMITS."""
    lim = limits_for(_admin())
    lim["max_pending"] = 1
    assert PLAN_LIMITS["pro"]["max_pending"] == 200


def test_a_labeler_is_not_given_an_unlimited_queue():
    """get_plan grants labelers pro for the training tools. That is not the
    same grant as admin, and the cap lift was asked for by admin."""
    labeler = {"id": "l1", "is_labeler": True, "subscription_status": "none"}
    assert get_plan(labeler) == "pro"
    assert limits_for(labeler)["max_pending"] == 200


def test_no_user_at_all_is_still_locked():
    """A deleted account holding a live session must not fall into the admin
    branch on its way to nothing."""
    assert limits_for(None)["max_pending"] == 0


# ── the paths that actually enforce it ───────────────────────────────────────

def test_pending_room_reports_the_lifted_cap(monkeypatch, tmp_path):
    """The processor checks this BEFORE creating the Twitch clip. If it still
    said 200, an admin's clips would be refused before anything else ran."""
    from src.dashboard import api
    from src.auth import users as user_store

    monkeypatch.setattr(user_store, "get_by_id", lambda uid: _admin())
    monkeypatch.setattr(api, "_clips", {
        f"c{i}": {"id": f"c{i}", "user_id": "a1", "status": "pending"}
        for i in range(500)})
    used, cap = api.pending_room("a1")
    assert used == 500
    assert cap == UNLIMITED_PENDING
    assert used < cap, "an admin 500 clips deep would still be refused"


def test_a_clip_past_the_pro_cap_is_kept_for_an_admin(monkeypatch, tmp_path):
    """The second check, in notify_clip_ready, guards the race. It reads the
    cap separately, so it needed the same lift and is verified separately."""
    import asyncio
    from src.dashboard import api
    from src.auth import users as user_store
    from src.stats import stream_stats as ss

    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)
    monkeypatch.setattr(api, "increment_clip_counter", lambda: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: _admin())

    async def _bcast(payload, user_id=None):
        pass
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api, "_clips", {
        f"c{i}": {"id": f"c{i}", "user_id": "a1", "status": "pending",
                  "channel": "aceu", "created_at": 1000.0 + i}
        for i in range(300)})          # well past pro's 200

    asyncio.run(api.notify_clip_ready(
        {"id": "new", "user_id": "a1", "channel": "aceu", "status": "pending",
         "created_at": 9999.0, "platform": "twitch",
         "twitch_url": "https://clips.twitch.tv/N"}))
    assert "new" in api._clips, "an admin's clip was dropped for a full queue"


def test_the_cap_is_lifted_in_one_place_only():
    """A second special-case elsewhere is how these drift apart: one path
    stops refusing and another keeps refusing, and the difference only shows
    up under load. Enforcement must keep reading limits_for()."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.pending_room)
    assert "limits_for" in src, "pending_room stopped reading the shared limits"
    assert "is_admin" not in src, \
        "pending_room grew its own admin case — lift it in limits_for instead"


# ── it survives the trip to the browser ──────────────────────────────────────

def test_the_cap_serialises_to_valid_json():
    """math.inf would emit the bare token Infinity, which JSON.parse rejects —
    every /me fetch would fail for exactly the account that has to be able to
    fix things."""
    import json
    payload = json.dumps({"plan_limits": limits_for(_admin())})
    assert "Infinity" not in payload and "NaN" not in payload
    assert json.loads(payload)["plan_limits"]["max_pending"] == UNLIMITED_PENDING


def test_the_dashboard_names_the_cap_instead_of_printing_it():
    """"1000000000 pending clips" reads as a bug to whoever sees it."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "'unlimited'" in DASHBOARD_HTML
    assert "max_pending >= 1000000000" in DASHBOARD_HTML, \
        "the sentinel is rendered as a raw number"


def test_the_frontend_threshold_matches_the_backend_sentinel():
    """Two literals that have to agree. If UNLIMITED_PENDING is ever changed
    without the template, the dashboard silently starts printing the number."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert f"max_pending >= {UNLIMITED_PENDING}" in DASHBOARD_HTML
