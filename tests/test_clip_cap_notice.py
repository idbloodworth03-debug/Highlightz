"""Telling a free user the pending cap cost them a clip.

The point of this is conversion, which is exactly why the wording has to be
right. The cap does NOT make us miss a clip: the new clip is saved and the
OLDEST UNREVIEWED one is deleted to make room. A user can disprove "we missed a
clip" by glancing at their queue and seeing the new one sitting there — and a
sales message they can catch lying is worth less than no message.
"""

import base64
import json as _j

import pytest
from itsdangerous import TimestampSigner

from src.stats import stream_stats as ss

FRONTEND = __import__("pathlib").Path(__file__).resolve().parent.parent / "src/dashboard/aurora_html.py"
SRC = FRONTEND.read_text()


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    yield


def _clip(uid="u1", cid="c1"):
    return {"id": cid, "user_id": uid, "channel": "aceu", "created_at": 1000.0}


def test_evictions_are_counted_from_the_ledger_not_a_memory_counter():
    """It has to survive a restart — the cap fires while the user is away, and
    a counter in memory would be zero by the time they open the tab."""
    import time
    for i in range(3):
        ss.record(ss.EXPIRED, _clip(cid=f"c{i}"))
    assert ss.evictions_since("u1", time.time() - 3600) == 3
    ss._LOG_FILE.touch()          # nothing cached; re-read from disk
    assert ss.evictions_since("u1", time.time() - 3600) == 3


def test_only_cap_evictions_count_not_rejections():
    """A clip the user threw away is not a clip the cap took."""
    import time
    ss.record(ss.EXPIRED, _clip(cid="a"))
    ss.record(ss.REJECTED, _clip(cid="b"))
    ss.record(ss.APPROVED, _clip(cid="c"))
    ss.record(ss.CAUGHT, _clip(cid="d"))
    assert ss.evictions_since("u1", time.time() - 3600) == 1


def test_the_window_is_respected():
    import time
    ss.record(ss.EXPIRED, _clip())
    assert ss.evictions_since("u1", time.time() + 60) == 0


def test_one_users_losses_are_not_anothers():
    import time
    ss.record(ss.EXPIRED, _clip(uid="mine"))
    ss.record(ss.EXPIRED, _clip(uid="theirs"))
    assert ss.evictions_since("mine", time.time() - 3600) == 1


# ── the wording ──────────────────────────────────────────────────────────────

def test_the_notice_says_a_clip_was_deleted_not_missed():
    """The load-bearing test. The cap deletes the OLDEST unreviewed clip; the
    new one is kept. Claiming we missed it is both false and checkable."""
    review = SRC[SRC.index("function ReviewScreen("):SRC.index("function LandingScreen(")]
    banner = review[review.index("rd-lost"):review.index("rd-stats")]
    assert "was deleted to make room" in banner or "were deleted to make room" in banner
    for lie in ("missed a clip", "could not clip", "we missed", "failed to clip"):
        assert lie not in banner.lower(), f"the notice claims {lie!r}, which is false"


def test_the_notice_names_the_next_tier_and_its_real_limit():
    """An upgrade prompt that does not say what you get is just a complaint."""
    review = SRC[SRC.index("function ReviewScreen("):SRC.index("function LandingScreen(")]
    assert "next_limit" in review and "next_price" in review
    assert "/billing/paywall" in review


def test_a_pro_user_is_told_to_review_rather_than_sold_to():
    """There is no tier above Pro. Showing them an upgrade button would be a
    dead end, so the copy switches to the thing they can actually do."""
    review = SRC[SRC.index("function ReviewScreen("):SRC.index("function LandingScreen(")]
    assert "Review or approve some to free up space" in review
    assert "{nextPlan && <a className=\"rd-btn grad\"" in review, \
        "the upgrade button is not conditional on a next tier existing"


def test_the_count_reaches_me_so_the_notice_survives_a_reload():
    """The broadcast is the live nudge. Without the /me field the notice
    vanishes on refresh, which is when most people would actually see it."""
    import inspect
    from src.dashboard import api
    assert "clips_lost_24h" in inspect.getsource(api.me)
    assert "clips_lost_24h" in SRC, "the frontend never reads it"


def test_zero_losses_clears_a_stale_notice():
    """Otherwise the banner sticks around after the user frees up space, which
    turns a true warning into a permanent nag."""
    assert "data.clips_lost_24h === 0) setLostClips(null)" in SRC


def test_the_evicted_event_has_a_handler():
    assert "msg.event==='clip_evicted'" in SRC


# ── end to end ───────────────────────────────────────────────────────────────

def test_me_reports_the_losses(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    people = {"free": {"id": "free", "username": "f", "subscription_status": "none"}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    for i in range(4):
        ss.record(ss.EXPIRED, _clip(uid="free", cid=f"c{i}"))

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "free", "subscription_status": "none"}).encode())).decode())
    me = c.get("/me").json()
    assert me["clips_lost_24h"] == 4
    assert me["plan"] == "free"
    assert me["plan_limits"]["max_pending"] == 15


def test_the_next_tier_reaches_me_so_the_reload_path_can_sell():
    """Found in the browser: on a PAGE LOAD the banner had no next-tier data
    and fell back to "review some to free up space" — never mentioning the
    upgrade. The reload path is how most people will actually see this."""
    import inspect
    from src.dashboard import api
    assert "next_plan" in inspect.getsource(api.me)
    assert "(data.next_plan||{}).price" in SRC, "the frontend ignores it on reload"


def test_pro_has_no_next_tier():
    from src.dashboard import api
    assert api._next_tier({"subscription_status": "active", "plan": "pro"}) is None
    nxt = api._next_tier({"subscription_status": "none"})
    assert nxt["plan"] == "starter" and nxt["max_pending"] == 50 and nxt["price"] == 10
