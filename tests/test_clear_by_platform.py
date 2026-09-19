"""Clearing and culling stay on one side of the Twitch/Kick switch.

THE BUG THIS FILE EXISTS TO PREVENT COMING BACK. Clip Review only ever shows
the active platform — `platformClips` in aurora_html.py filters the queue
before the screen sees it — and the Clear queue button counts that filtered
set for its own label. The endpoint did not: it removed every pending clip
the account had. So a button reading "Clear 12 clips" deleted those twelve
AND a Kick queue the user could not see from the screen they pressed it on.
`Cull low scores` sits on the same screen and had the same defect.

An undo entry existed, which is not the point: a destructive action whose
count disagrees with what it destroys should not need one.

Reported 2026-09-19: "I need the clip clear to only clear clips on that
specific platform (Kick or Twitch)."
"""

import pytest


def _clip(cid, uid="u1", platform="twitch", status="pending", score=90, **kw):
    c = {"id": cid, "user_id": uid, "status": status, "platform": platform,
         "trigger_score": score, "channel": "c", "created_at": 1.0}
    c.update(kw)
    return c


@pytest.fixture
def api(monkeypatch):
    from src.dashboard import api as mod
    clips = {}
    monkeypatch.setattr(mod, "_clips", clips)
    monkeypatch.setattr(mod, "_save_clips", lambda: None)

    async def _noop(*a, **k):
        return None

    monkeypatch.setattr(mod, "broadcast", _noop)
    monkeypatch.setattr(mod._dismissed, "dismiss", lambda *a, **k: None)
    monkeypatch.setattr(mod.undo, "push", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_hold_files", lambda clips: {})
    from src.stats import stream_stats
    monkeypatch.setattr(stream_stats, "record", lambda *a, **k: None)
    return mod, clips


def _run(coro):
    import asyncio
    return asyncio.run(coro)


class Req:
    def __init__(self, uid="u1"):
        self.session = {"user_id": uid}


@pytest.fixture
def call(api, monkeypatch):
    mod, clips = api
    monkeypatch.setattr(mod, "_current_user_id", lambda r: "u1")
    return mod, clips


# ── clearing ─────────────────────────────────────────────────────────────────

def test_clearing_twitch_leaves_kick_alone(call):
    mod, clips = call
    for i in range(3):
        clips[f"t{i}"] = _clip(f"t{i}", platform="twitch")
    for i in range(2):
        clips[f"k{i}"] = _clip(f"k{i}", platform="kick")

    out = _run(mod.clear_pending_clips(Req(), platform="twitch"))
    assert out["removed"] == 3
    assert set(clips) == {"k0", "k1"}, "a Twitch clear took the Kick queue with it"


def test_clearing_kick_leaves_twitch_alone(call):
    mod, clips = call
    clips["t0"] = _clip("t0", platform="twitch")
    clips["k0"] = _clip("k0", platform="kick")
    assert _run(mod.clear_pending_clips(Req(), platform="kick"))["removed"] == 1
    assert set(clips) == {"t0"}


def test_a_clip_from_before_kick_existed_counts_as_twitch(call):
    """Old records have no `platform` key at all. Everywhere else in the file
    that defaults to twitch, so reading it as "" here would make those clips
    invisible to a Twitch clear and immortal in the queue."""
    mod, clips = call
    clips["old"] = {"id": "old", "user_id": "u1", "status": "pending",
                    "channel": "c", "created_at": 1.0}
    assert _run(mod.clear_pending_clips(Req(), platform="twitch"))["removed"] == 1
    assert clips == {}


def test_no_platform_still_clears_everything(call):
    """Account deletion and any non-UI caller want the whole queue gone."""
    mod, clips = call
    clips["t0"] = _clip("t0", platform="twitch")
    clips["k0"] = _clip("k0", platform="kick")
    assert _run(mod.clear_pending_clips(Req()))["removed"] == 2
    assert clips == {}


def test_approved_clips_are_still_untouchable(call):
    """The older rule this must not have broken: approved clips are the
    library, not the inbox."""
    mod, clips = call
    clips["a"] = _clip("a", platform="twitch", status="approved")
    clips["p"] = _clip("p", platform="twitch")
    _run(mod.clear_pending_clips(Req(), platform="twitch"))
    assert set(clips) == {"a"}


def test_another_users_clips_are_never_touched(call):
    mod, clips = call
    clips["mine"] = _clip("mine", uid="u1", platform="twitch")
    clips["theirs"] = _clip("theirs", uid="u2", platform="twitch")
    _run(mod.clear_pending_clips(Req(), platform="twitch"))
    assert set(clips) == {"theirs"}


def test_a_misspelt_platform_is_refused_not_silently_a_no_op(call):
    """"?platform=twich" quietly clearing nothing is indistinguishable from an
    empty queue, and the user would press it again."""
    from fastapi import HTTPException
    mod, clips = call
    clips["t0"] = _clip("t0")
    with pytest.raises(HTTPException) as e:
        _run(mod.clear_pending_clips(Req(), platform="twich"))
    assert e.value.status_code == 400
    assert set(clips) == {"t0"}


# ── culling, the same screen and the same defect ─────────────────────────────

def test_culling_is_scoped_to_the_platform_too(call):
    mod, clips = call
    clips["t_low"] = _clip("t_low", platform="twitch", score=10)
    clips["k_low"] = _clip("k_low", platform="kick", score=10)
    body = mod.BulkCullBody(min_score=50, platform="twitch")
    out = _run(mod.bulk_cull_clips(Req(), body))
    assert out["removed"] == 1
    assert set(clips) == {"k_low"}, "a Twitch cull reached into Kick"


def test_culling_still_spares_crowd_suggestions(call):
    """They carry score 0 because nothing scored them, so any threshold above
    zero would delete every one."""
    mod, clips = call
    clips["s"] = _clip("s", platform="twitch", score=0, suggested=True)
    clips["low"] = _clip("low", platform="twitch", score=10)
    _run(mod.bulk_cull_clips(Req(), mod.BulkCullBody(min_score=50, platform="twitch")))
    assert set(clips) == {"s"}


def test_cull_without_a_platform_still_covers_both(call):
    mod, clips = call
    clips["t"] = _clip("t", platform="twitch", score=10)
    clips["k"] = _clip("k", platform="kick", score=10)
    assert _run(mod.bulk_cull_clips(Req(), mod.BulkCullBody(min_score=50)))["removed"] == 2


# ── the frontend sends it ────────────────────────────────────────────────────

def test_the_buttons_send_the_platform_they_are_showing():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert "'/clips/clear-pending?platform=' + encodeURIComponent(activePlatform)" in h
    assert "min_score: thresh, platform: activePlatform" in h
    # And the screen is given it, or both default quietly to twitch and a Kick
    # user clears the wrong queue.
    assert "clips:platformClips,activePlatform," in h


def test_the_count_and_the_action_read_the_same_set():
    """The label says "Clear N clips" from platformClips; the request must be
    scoped to the same platform or the number is a lie again."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert "Object.entries(clips).filter(([,c])=>c.platform===activePlatform)" in h
    assert "function ClearQueueButton({ pending, activePlatform" in h
