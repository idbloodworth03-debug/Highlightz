"""Clip retention: 30 days approved, 7 days pending.

The existing tests in test_maintenance_loops.py check the sweep's LOOP SHAPE —
that it does not sleep before its first pass, and does sleep at the end. None
of them run a pass, so none would notice if the sweep deleted the wrong clips
or every clip. These run it.

WHAT EXPIRY COSTS, since it decides whether 7 days is reasonable:

  live clips  — nothing but a row. The clip was created on Twitch with the
                user's own token and stays on their account; Highlightz hosts
                no video, so our record is not the clip.
  VOD moments — the finding, recoverable only by rescanning. They get 7 days
                anyway because Twitch keeps the VOD they point at for 7 days
                on most channels (14 Affiliate, 60 Partner), so an older
                moment mostly links to something already gone.
"""

import asyncio
import time

import pytest


class _StopSweep(Exception):
    """Raised in place of the trailing sleep to end the loop after one pass."""


@pytest.fixture
def sweep(monkeypatch, tmp_path):
    """Run exactly one pass of the real sweep against a clip set you supply."""
    from src.dashboard import api
    from src import main

    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)

    sent = []

    async def _bcast(payload, user_id=None):
        sent.append((payload, user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)

    calls = {"n": 0}

    async def _sleep(secs):
        # First call is the settle delay before the first pass; skip it. The
        # second is the trailing daily sleep, which ends the run.
        calls["n"] += 1
        if calls["n"] >= 2:
            raise _StopSweep
    monkeypatch.setattr(main.asyncio, "sleep", _sleep)

    def run(clips):
        api._clips.clear()
        api._clips.update({c["id"]: c for c in clips})
        sent.clear()
        with pytest.raises(_StopSweep):
            asyncio.run(main.auto_delete_old_clips())
        return set(api._clips), sent

    yield run
    api._clips.clear()


NOW = time.time()
DAY = 86400.0


def _clip(cid, status, age_days, uid="u1", **extra):
    return {"id": cid, "user_id": uid, "status": status, "channel": "aceu",
            "created_at": NOW - age_days * DAY, "platform": "twitch", **extra}


# ── pending: the new 7-day rule ──────────────────────────────────────────────

def test_a_pending_clip_older_than_seven_days_is_expired(sweep):
    left, _ = sweep([_clip("old", "pending", 8)])
    assert left == set()


def test_a_pending_clip_inside_seven_days_is_kept(sweep):
    left, _ = sweep([_clip("fresh", "pending", 6)])
    assert left == {"fresh"}


def test_the_boundary_is_seven_days_not_six_or_eight(sweep):
    """Pins the actual number. A test that only checks 1 day vs 60 passes for
    any threshold in between."""
    left, _ = sweep([_clip("d6", "pending", 6),
                     _clip("d8", "pending", 8)])
    assert left == {"d6"}, "the pending window is not 7 days"


def test_a_brand_new_pending_clip_is_never_touched(sweep):
    left, _ = sweep([_clip("new", "pending", 0)])
    assert left == {"new"}


# ── approved: unchanged at 30 days ───────────────────────────────────────────

def test_an_approved_clip_still_gets_thirty_days(sweep):
    """The change must not have shortened the library along with the queue."""
    left, _ = sweep([_clip("d8", "approved", 8),
                     _clip("d29", "approved", 29),
                     _clip("d31", "approved", 31)])
    assert left == {"d8", "d29"}, "approved retention moved"


def test_an_approved_clip_is_not_expired_on_the_pending_clock(sweep):
    """THE regression the shared sweep invites: one rule accidentally applied
    to both statuses would wipe every library older than a week."""
    left, _ = sweep([_clip("keep", "approved", 20)])
    assert left == {"keep"}


# ── what must not be swept ───────────────────────────────────────────────────

def test_an_unknown_status_is_never_expired(sweep):
    """The default is infinity on purpose: a status added later must not
    silently inherit a deletion policy nobody chose for it."""
    left, _ = sweep([_clip("weird", "processing", 400),
                     _clip("none", None, 400)])
    assert left == {"weird", "none"}


def test_a_clip_with_no_created_at_is_not_expired(sweep):
    """Missing timestamp reads as age zero, not as infinitely old. Getting
    this backwards deletes the whole set on the first pass."""
    c = _clip("nots", "pending", 0)
    c.pop("created_at")
    left, _ = sweep([c])
    assert left == {"nots"}


def test_nothing_is_recorded_in_the_stats_ledger(sweep, monkeypatch, tmp_path):
    """An expired clip is not an approval and emphatically not a rejection —
    the user never rendered a verdict. Writing either would move the keep rate
    shown to streamers on the strength of clips nobody ever looked at."""
    from src.stats import stream_stats as ss
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    sweep([_clip("old", "pending", 30), _clip("older", "approved", 90)])
    assert not (tmp_path / "stats.jsonl").exists() or \
        (tmp_path / "stats.jsonl").read_text().strip() == "", \
        "expiry wrote a verdict into the clip record"


# ── VOD moments ──────────────────────────────────────────────────────────────

def test_a_pending_vod_moment_expires_on_the_same_clock(sweep):
    """Deliberate, and the reason is in the constant's comment: Twitch keeps
    the VOD it points at for 7 days on most channels, so an older moment links
    to something already gone."""
    left, _ = sweep([_clip("m", "pending", 9, is_vod_moment=True,
                           twitch_url="https://www.twitch.tv/videos/1?t=30")])
    assert left == set()


def test_an_approved_vod_moment_keeps_the_full_thirty_days(sweep):
    left, _ = sweep([_clip("m", "approved", 20, is_vod_moment=True)])
    assert left == {"m"}


# ── the realtime contract (CLAUDE.md) ────────────────────────────────────────

def test_every_expired_clip_is_broadcast_scoped_to_its_owner(sweep):
    """A clip vanishing from the store without a clip_removed leaves it on
    screen until the tab reloads, which is the failure mode the whole realtime
    rule exists to prevent."""
    left, sent = sweep([_clip("a", "pending", 9, uid="u1"),
                        _clip("b", "approved", 40, uid="u2")])
    assert left == set()
    assert ({"event": "clip_removed", "clip_id": "a"}, "u1") in sent
    assert ({"event": "clip_removed", "clip_id": "b"}, "u2") in sent


def test_nothing_is_broadcast_when_nothing_expired(sweep):
    _, sent = sweep([_clip("fresh", "pending", 1)])
    assert sent == []


def test_one_users_expiry_is_not_broadcast_to_another(sweep):
    _, sent = sweep([_clip("a", "pending", 9, uid="u1")])
    assert [uid for _, uid in sent] == ["u1"]


def test_the_clip_removed_event_has_a_frontend_handler():
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "clip_removed" in DASHBOARD_HTML, \
        "the sweep emits clip_removed and nothing handles it"


# ── the retention table itself ───────────────────────────────────────────────

def test_the_retention_values_are_what_they_claim():
    from src.main import CLIP_RETENTION
    assert CLIP_RETENTION["pending"] == 7 * 86400
    assert CLIP_RETENTION["approved"] == 30 * 86400


def test_rejected_is_absent_from_the_table():
    """/reject deletes at the moment of rejection, so an entry here would be
    dead config implying a policy that never runs."""
    from src.main import CLIP_RETENTION
    assert "rejected" not in CLIP_RETENTION


def test_the_sweep_reads_the_table_rather_than_a_literal():
    """Two places to change a number is one place to forget."""
    import inspect
    from src import main
    src = inspect.getsource(main.auto_delete_old_clips)
    assert "CLIP_RETENTION" in src
    assert "30 * 86400" not in src and "7 * 86400" not in src, \
        "the sweep hardcodes an age instead of reading CLIP_RETENTION"
