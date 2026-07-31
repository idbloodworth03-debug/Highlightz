"""
Viewer-clip watcher: crowd highlight labels, recorded for LEARNING only.

Phase 0 measured Twitch's clip-visibility lag at ~167s median (p90 369s), so
acting on a viewer clip would capture the aftermath rather than the moment.
The <45s bar for "trigger" was set before that data existed; it failed, so
this path observes and never fires. These tests lock the properties that make
that safe and useful.
"""

import json
import time

from src.trigger import viewer_clips
from src.trigger.engine import TriggerEngine


def _engine():
    async def _noop(*a, **k):
        pass
    return TriggerEngine(channel="x", on_trigger=_noop)


def test_score_history_lets_a_late_clip_find_its_moment():
    """The whole reason latency stops mattering: we look backward.

    A clip surfacing 3 minutes late must still pair with what we scored when
    the viewer actually clipped — not with 'now'.
    """
    e = _engine()
    now = time.time()
    for i in range(600):                       # 10 minutes of readings
        e._score_history.append((now - 600 + i, float(i % 100)))

    # A moment 167s ago (the measured median lag) is still recoverable.
    assert e.score_at(now - 167) is not None
    # And it returns the reading from THEN, not the latest one.
    e2 = _engine()
    e2._score_history.append((now - 300, 82.0))
    e2._score_history.append((now, 11.0))
    assert e2.score_at(now - 300) == 82.0

    # Beyond tolerance there is no honest answer, so it says so rather than
    # pairing a clip with an unrelated score.
    assert e2.score_at(now - 5000) is None
    assert _engine().score_at(now) is None      # no history at all


def test_history_is_bounded():
    """A long stream must not grow this without limit."""
    e = _engine()
    now = time.time()
    for i in range(5000):
        e._score_history.append((now + i, 50.0))
    assert len(e._score_history) <= 1200


def test_poll_gate_is_per_channel_not_per_user():
    """Helix budget is per client-id, shared across every user. Five users
    watching one streamer must cost ONE poll, not five."""
    viewer_clips._last_poll.clear()
    now = time.time()
    assert viewer_clips.due("jynxzi", now) is True
    viewer_clips._last_poll["jynxzi"] = now
    # Every other worker on the same channel is now gated off.
    assert viewer_clips.due("jynxzi", now + 1) is False
    assert viewer_clips.due("jynxzi", now + 30) is False
    # A different channel is unaffected.
    assert viewer_clips.due("lacy", now + 1) is True
    # And it reopens after the interval.
    assert viewer_clips.due("jynxzi", now + viewer_clips.POLL_INTERVAL + 1) is True


def test_our_own_clips_are_never_recorded(tmp_path, monkeypatch):
    """Our clips are real Twitch clips in the same API. Learning from them
    would be learning from ourselves — the single most dangerous failure mode
    in this feature."""
    import asyncio
    from unittest.mock import AsyncMock, patch

    monkeypatch.setattr(viewer_clips, "_LOG_FILE", tmp_path / "viewer_clips.jsonl")
    viewer_clips._seen.clear()
    viewer_clips._last_poll.clear()

    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 120))
    rows = [
        {"id": "viewer1", "creator_id": "999", "creator_name": "fan",
         "title": "insane clutch", "created_at": stamp},
        {"id": "ours_by_id", "creator_id": "42", "creator_name": "hzuser",
         "title": "ours", "created_at": stamp},
        {"id": "ours_by_slug", "creator_id": "777", "creator_name": "other",
         "title": "ours", "created_at": stamp},
    ]

    class _Resp:
        status = 200
        async def json(self): return {"data": rows}
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Sess:
        def get(self, *a, **k): return _Resp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    e = _engine()
    e._score_history.append((now - 120, 73.5))

    import src.output.twitch_clips as tc
    with patch.object(tc, "_get_app_token", new=AsyncMock(return_value="t")), \
         patch("aiohttp.ClientSession", lambda *a, **k: _Sess()):
        n = asyncio.run(viewer_clips.poll_and_record(
            "x", "123", e, our_creator_ids={"42"}, our_slugs={"ours_by_slug"}))

    assert n == 1, "only the genuine viewer clip should be recorded"
    written = [json.loads(l) for l in
               (tmp_path / "viewer_clips.jsonl").read_text().splitlines()]
    assert [r["clip_id"] for r in written] == ["viewer1"]
    # The pairing that makes it a label: what we scored when they clipped.
    assert written[0]["our_score"] == 73.5
    assert written[0]["lag"] > 0


def test_poll_failure_is_silent_and_harmless(tmp_path, monkeypatch):
    """Observation must never be able to disturb clipping."""
    import asyncio
    from unittest.mock import AsyncMock, patch
    monkeypatch.setattr(viewer_clips, "_LOG_FILE", tmp_path / "v.jsonl")
    viewer_clips._seen.clear(); viewer_clips._last_poll.clear()
    import src.output.twitch_clips as tc
    with patch.object(tc, "_get_app_token", new=AsyncMock(side_effect=RuntimeError("down"))):
        assert asyncio.run(viewer_clips.poll_and_record(
            "x", "123", _engine(), set(), set())) == 0
    assert not (tmp_path / "v.jsonl").exists()


def test_watcher_cannot_trigger_a_clip():
    """Locked by design: measured latency ruled the trigger out, and nothing
    in this module may create clips or move a score."""
    import inspect
    src = inspect.getsource(viewer_clips)
    for forbidden in ("create_clip", "on_trigger", "_last_score =", "trigger_threshold ="):
        assert forbidden not in src, f"watcher must not {forbidden!r}"


def test_score_window_takes_the_peak_not_the_endpoint():
    """A point sample at the clip's created_at is biased LOW.

    A viewer watches the moment land, THEN reaches for the button, so
    created_at stamps the aftermath — chat already calming. Scoring the bot
    against that instant makes it look like it missed moments it actually
    spiked on seconds earlier. The window asks the honest question: did our
    score ever cross the bar while the clipped moment was happening?
    """
    from src.trigger.engine import TriggerEngine
    eng = TriggerEngine.__new__(TriggerEngine)          # no I/O, no config
    from collections import deque
    eng._score_history = deque([
        (1000.0, 20.0),
        (1020.0, 88.0),      # the spike
        (1040.0, 30.0),
        (1060.0, 12.0),      # where the viewer's clip lands
    ])

    peak, n = eng.score_window(1000.0, 1060.0)
    assert peak == 88.0 and n == 4

    # The bias this exists to remove: sampling at the clip timestamp alone
    # reads 12, the window reads the 88 that actually happened.
    assert eng.score_at(1060.0, tolerance=5.0) == 12.0
    assert eng.score_window(1000.0, 1070.0)[0] == 88.0

    # Nothing in range is reported as no data, never as zero — "we weren't
    # watching" and "we scored 0" must not collapse into the same number.
    assert eng.score_window(2000.0, 2100.0) == (None, 0)


def test_recorded_peak_window_looks_backward_from_the_clip():
    """The moment precedes created_at, so the window must be asymmetric —
    mostly before the timestamp, barely after."""
    from src.trigger import viewer_clips as vc
    assert vc._PEAK_LOOKBACK > vc._PEAK_LOOKAHEAD
    # Wide enough to cover a Twitch clip (~30s) plus human reaction time.
    assert vc._PEAK_LOOKBACK >= 45.0
