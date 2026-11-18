"""When Twitch will not make the clip, keep our own recording (2026-11-18).

Owner: "can we have some sort of clipping being done for streams that cant be
clipped on twitch", then "B but make it only as a back up if those cases
happen otherwise use the normal clipping method".

Behind TWITCH_FILE_FALLBACK, off by default. Held here:
  * with the switch off, every refusal is handled exactly as it was;
  * Twitch is always asked first, and again after a window, so real Twitch
    clips come back by themselves;
  * a refusal or a failure becomes a file-only clip (the Kick shape, marked
    `file_only`) and the channel is not stopped;
  * the clip is stored only once its video is whole: no recording, no card;
  * never for a broadcaster who opted out, never without capture, never for
    Kick, never for an expired sign-in;
  * the rest of the product treats it as what it is: the sweeps, the fetch,
    the viewer, the standing notice, the Terms.
The clip queue below is the REAL run_clip_processor.
"""

import asyncio
import json
import os
import pathlib
import time

import pytest

import src.main as M
from config.settings import Settings, settings
from src.clips import files as clip_files
from src.dashboard import api as dash
from src.dashboard.aurora_html import DASHBOARD_HTML
from src.output import twitch_clips
from src.processor.clip_processor import ClipProcessor, PendingTwitchClip, TwitchAuthExpiredError
from src.processor.metadata import ClipMetadata
from src.queue.job_queue import ClipJob
from src.stats import clip_refusals as refusals


def _job(cid, uid="u1", channel="restricted", platform="twitch", manual=False):
    j = ClipJob(clip_id=cid, channel=channel, platform=platform, user_id=uid,
                trigger_score=80.0,
                trigger_signals=[{"type": "MANUAL", "value": 1.0}] if manual else [],
                chat_snapshot=[], stream_title="t", game="g", virality_score=0.0,
                clip_title="", pre_roll=30, post_roll=0)
    # Caught twenty seconds ago: the cut was due a while back, so a recording
    # that is not there is waited for only _FALLBACK_MIN_WAIT_S.
    j.created_at = time.time() - 20
    return j


class _Ok:
    """A clip Twitch made: what processor.start hands back for a Kick clip or
    a finished job."""
    def __init__(self, job):
        self.meta = ClipMetadata(id=job.clip_id, channel=job.channel, platform="twitch",
                                 user_id=job.user_id, twitch_url="https://clips.twitch.tv/x",
                                 twitch_clip_id="x")

    def to_dict(self):
        return self.meta.to_dict()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(clip_files, "_ROOT", tmp_path / "clipfiles")
    (tmp_path / "clipfiles").mkdir()
    monkeypatch.setattr(clip_files, "_DISCARDED", {})
    monkeypatch.setattr(refusals, "_FILE", tmp_path / "clip_refusals.json")
    monkeypatch.setattr(settings, "twitch_file_fallback", True)
    monkeypatch.setattr(settings, "clip_capture_enabled", True)
    monkeypatch.setattr("src.auth.optout.is_opted_out", lambda ch: ch == "optedout")
    for name in ("_title_automod_until", "_classification_until", "_not_authorized_until",
                 "_fallback_misses", "_twitch_in_flight"):
        monkeypatch.setattr(M, name, {})
    monkeypatch.setattr(M, "_FALLBACK_FILE_MARGIN_S", 0.0)
    monkeypatch.setattr(M, "_FALLBACK_MIN_WAIT_S", 0.15)
    monkeypatch.setattr(dash, "_files_landed", {})
    monkeypatch.setattr(dash, "_file_waiters", {})

    seen = {"asked": [], "stored": [], "stopped": [], "said": [], "discarded": [], "cues": [],
            "in_flight": []}
    monkeypatch.setattr(clip_files, "discard", lambda cid: seen["discarded"].append(cid))
    monkeypatch.setattr(dash, "pending_room", lambda uid: (0, 200))

    async def notify_missed(*a, **k):
        return None

    async def broadcast(event, user_id=None):
        seen["said"].append((event.get("event"), user_id, event.get("message", "")))

    async def stop(channel, uid):
        seen["stopped"].append((channel, uid))
        return True

    async def file_ready(clip_id):
        seen["cues"].append(clip_id)
        dash._note_file_landed(clip_id)

    monkeypatch.setattr(dash, "notify_clip_missed", notify_missed)
    monkeypatch.setattr(dash, "broadcast", broadcast)
    monkeypatch.setattr(dash, "stop_stream_internal", stop)
    monkeypatch.setattr(dash, "on_clip_file_ready", file_ready)

    def run(jobs, script, lands=(), kept=True, lands_after=0.03):
        """Push `jobs` through the real queue. `script` maps a clip id to what
        Twitch does with it: an exception to raise, "ok", or "never" (accepted,
        never finished). `lands` are the clip ids whose recording arrives."""
        jobs = list(jobs)

        class Scripted(ClipProcessor):
            async def start(self, job):
                seen["asked"].append(job.clip_id)
                what = script.get(job.clip_id, "ok")
                if isinstance(what, BaseException):
                    raise what
                if what == "never":
                    class Never(PendingTwitchClip):
                        async def finish(self):
                            raise twitch_clips.ClipNotReadyError("never finished")
                    return Never(ClipMetadata(id=job.clip_id), "slug", job.channel)
                return _Ok(job)
        monkeypatch.setattr(M, "ClipProcessor", Scripted)

        class Q:
            async def pop(self, timeout=5):
                await asyncio.sleep(0)
                return jobs.pop(0) if jobs else None
        monkeypatch.setattr(M, "_queue", Q())

        async def ready(clip):
            seen["in_flight"].append(dict(M._twitch_in_flight))
            if kept:
                seen["stored"].append(clip)
            return kept
        monkeypatch.setattr(dash, "notify_clip_ready", ready)

        async def go():
            loop = asyncio.get_running_loop()
            for cid in lands:
                loop.call_later(lands_after, dash._note_file_landed, cid)
            task = asyncio.create_task(M.run_clip_processor())
            end = time.monotonic() + 3.0
            await asyncio.sleep(0.1)
            while (jobs or M._finishing) and time.monotonic() < end:
                await asyncio.sleep(0.02)
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        asyncio.run(go())
        return seen
    return run


NOT_ALLOWED = twitch_clips.ClipNotAuthorizedError("restricted")
AUTOMOD = twitch_clips.ClipTitleRejectedError("title")
UNCLASSIFIED = twitch_clips.ClipClassificationError("ccl")


# ── off by default, and then nothing is different ────────────────────────────

def test_the_switch_is_off_unless_somebody_turns_it_on():
    assert Settings.model_fields["twitch_file_fallback"].default is False


@pytest.mark.parametrize("off", ["switch", "capture"])
def test_with_it_off_a_restricted_channel_is_stopped_as_it_always_was(rig, monkeypatch, off):
    monkeypatch.setattr(settings, "twitch_file_fallback" if off == "switch" else "clip_capture_enabled", False)
    seen = rig([_job("a")], {"a": NOT_ALLOWED}, lands=["a"])
    assert seen["stopped"] == [("restricted", "u1")]
    assert seen["stored"] == [] and seen["discarded"] == ["a"]
    assert any("Monitoring stopped." in m for _e, _u, m in seen["said"])
    assert M._not_authorized_until == {}


def test_with_it_off_a_moment_in_an_automod_window_is_skipped_as_it_always_was(rig, monkeypatch):
    monkeypatch.setattr(settings, "twitch_file_fallback", False)
    seen = rig([_job("a"), _job("b")], {"a": AUTOMOD}, lands=["a", "b"])
    assert seen["asked"] == ["a"]                       # b never reached Twitch
    assert seen["stored"] == [] and seen["discarded"] == ["a", "b"]
    assert any("resumes on its own once the streamer changes their title" in m for _e, _u, m in seen["said"])


def test_with_it_off_a_failure_is_a_missed_clip_as_it_always_was(rig, monkeypatch):
    monkeypatch.setattr(settings, "twitch_file_fallback", False)
    seen = rig([_job("a"), _job("b")], {"a": RuntimeError("create failed"), "b": "never"}, lands=["a", "b"])
    assert seen["stored"] == [] and sorted(seen["discarded"]) == ["a", "b"]
    assert sum("couldn't be captured" in m for _e, _u, m in seen["said"]) == 2


# ── a restricted channel ─────────────────────────────────────────────────────

def test_a_restricted_channel_is_recorded_instead_of_stopped(rig):
    seen = rig([_job("a")], {"a": NOT_ALLOWED}, lands=["a"])
    assert seen["asked"] == ["a"], "Twitch was not asked first"
    assert seen["stopped"] == [] and seen["discarded"] == []
    (clip,) = seen["stored"]
    assert clip["id"] == "a" and clip["platform"] == "twitch" and clip["status"] == "pending"
    assert clip["file_only"] is True and clip["fallback_reason"] == "not_authorized"
    assert clip["twitch_url"] == "" and clip["embed_url"] == "" and clip["twitch_clip_id"] == ""
    assert clip["platform_url"] == "https://www.twitch.tv/restricted"
    assert clip["duration_seconds"] == 30.0
    # The user is told what is happening, and what would bring Twitch clips back.
    (toast,) = [m for e, _u, m in seen["said"] if e == "clip_failed"]
    assert "keeps its own recording of each moment instead" in toast
    assert "Following or subscribing" in toast and "Monitoring stopped" not in toast
    # The standing notice and the admin list still say Twitch refused.
    assert refusals.rows_for_user("u1")[0]["reason"] == refusals.NOT_AUTHORIZED
    # What a finished cut does for a record (picture, library, Autopilot) is done.
    assert seen["cues"] == ["a"]


def test_twitch_is_not_asked_again_inside_the_window_and_the_user_is_told_once(rig):
    seen = rig([_job("a"), _job("b"), _job("c")], {"a": NOT_ALLOWED}, lands=["a", "b", "c"])
    assert seen["asked"] == ["a"], "a channel known to be restricted was asked every time"
    assert [c["id"] for c in seen["stored"]] == ["a", "b", "c"]
    assert all(c["file_only"] and c["fallback_reason"] == "not_authorized" for c in seen["stored"])
    assert sum(e == "clip_failed" for e, _u, _m in seen["said"]) == 1


def test_after_the_window_twitch_is_asked_again_and_real_clips_come_back(rig):
    """The user followed the channel, or the streamer lifted it."""
    rig([_job("a")], {"a": NOT_ALLOWED}, lands=["a"])
    assert ("u1", "restricted") in M._not_authorized_until
    M._not_authorized_until[("u1", "restricted")] = time.time() - 1          # the hour passed
    seen = rig([_job("b")], {"b": "ok"})
    assert seen["asked"] == ["a", "b"]
    assert seen["stored"][-1]["file_only"] is False and seen["stored"][-1]["twitch_url"]
    assert ("u1", "restricted") not in M._not_authorized_until
    assert refusals.rows_for_user("u1") == [], "the notice outlived the refusal"


def test_still_restricted_after_the_window_starts_another_one(rig):
    rig([_job("a")], {"a": NOT_ALLOWED}, lands=["a"])
    M._not_authorized_until[("u1", "restricted")] = time.time() - 1
    seen = rig([_job("b"), _job("c")], {"b": NOT_ALLOWED}, lands=["b", "c"])
    assert seen["asked"] == ["a", "b"] and len(seen["stored"]) == 3
    assert M._not_authorized_until[("u1", "restricted")] > time.time() + M._NOT_AUTHORIZED_RETRY_S - 5


def test_the_restriction_is_about_who_is_asking(rig):
    """A follower may clip where a stranger may not: another account watching
    the same channel is still asked the normal way."""
    seen = rig([_job("a", uid="stranger"), _job("b", uid="follower")], {"a": NOT_ALLOWED}, lands=["a"])
    assert seen["asked"] == ["a", "b"]
    by = {c["id"]: c for c in seen["stored"]}
    assert by["a"]["file_only"] is True and by["b"]["file_only"] is False


# ── the other refusals, and plain failures ───────────────────────────────────

@pytest.mark.parametrize("exc, reason, window", [
    (AUTOMOD, "title_automod", "_title_automod_until"),
    (UNCLASSIFIED, "classification", "_classification_until"),
])
def test_a_refused_title_or_rating_is_recorded_now_and_for_the_whole_window(rig, exc, reason, window):
    seen = rig([_job("a"), _job("b")], {"a": exc}, lands=["a", "b"])
    assert seen["asked"] == ["a"]                       # the window still spares Twitch the call
    assert [(c["id"], c["fallback_reason"]) for c in seen["stored"]] == [("a", reason), ("b", reason)]
    assert "restricted" in getattr(M, window) and seen["stopped"] == [] and seen["discarded"] == []
    (toast,) = [m for e, _u, m in seen["said"] if e == "clip_failed"]
    assert "keeps its own recording of each moment instead" in toast


def test_a_clip_twitch_accepts_and_never_finishes_is_kept_as_the_recording(rig):
    seen = rig([_job("a")], {"a": "never"}, lands=["a"])
    (clip,) = seen["stored"]
    assert clip["file_only"] is True and clip["fallback_reason"] == "twitch_failed"
    assert seen["discarded"] == [] and not [1 for e, _u, _m in seen["said"] if e == "clip_failed"]
    assert M._twitch_in_flight == {}


def test_create_clip_failing_keeps_the_recording_and_starts_no_window(rig):
    seen = rig([_job("a"), _job("b")], {"a": RuntimeError("Twitch clip creation failed")}, lands=["a"])
    assert seen["asked"] == ["a", "b"], "one failure must not stop Twitch being asked next time"
    by = {c["id"]: c for c in seen["stored"]}
    assert by["a"]["fallback_reason"] == "twitch_failed" and by["b"]["file_only"] is False


def test_the_recording_that_landed_before_twitch_gave_up_is_used_at_once(rig):
    """A clip Twitch takes a minute to give up on was cut long before."""
    dash._note_file_landed("a")
    t0 = time.monotonic()
    seen = rig([_job("a")], {"a": "never"})
    assert [c["id"] for c in seen["stored"]] == ["a"] and time.monotonic() - t0 < 2.5


# ── no recording, no card ────────────────────────────────────────────────────

def test_a_failure_with_no_recording_is_a_missed_clip_and_nothing_is_stored(rig):
    seen = rig([_job("a")], {"a": RuntimeError("create failed")})
    assert seen["stored"] == [] and seen["discarded"] == ["a"]
    assert sum("couldn't be captured" in m for _e, _u, m in seen["said"]) == 1
    assert M._twitch_in_flight == {}


def test_a_window_with_no_recording_stays_as_quiet_as_the_skip_it_replaces(rig):
    seen = rig([_job("a"), _job("b"), _job("c")], {"a": AUTOMOD})
    assert seen["stored"] == [] and sorted(seen["discarded"]) == ["a", "b", "c"]
    assert sum(e == "clip_failed" for e, _u, _m in seen["said"]) == 1    # the one that names the cause


def test_a_restricted_channel_whose_recording_never_arrives_is_stopped_after_three(rig):
    """Scoring, firing and producing nothing is what stopping a restricted
    channel was for. One miss is a hiccup; three in a row is that again."""
    seen = rig([_job("a"), _job("b")], {"a": NOT_ALLOWED})
    assert seen["stopped"] == [] and M._fallback_misses == {("u1", "restricted"): 2}
    seen = rig([_job("c")], {})
    assert seen["stopped"] == [("restricted", "u1")]
    assert any("Monitoring stopped." in m for _e, _u, m in seen["said"])
    assert M._fallback_misses == {} and M._not_authorized_until == {}


def test_a_recording_that_does_arrive_starts_the_count_again(rig):
    rig([_job("a"), _job("b")], {"a": NOT_ALLOWED})
    seen = rig([_job("c")], {}, lands=["c"])
    assert M._fallback_misses == {} and seen["stopped"] == []


def test_the_clip_now_button_never_counts_against_the_channel(rig):
    """A manual clip is queued by the web process; no stream worker cuts a
    file for it, so its recording never arrives whatever the channel is."""
    rig([_job("a")], {"a": NOT_ALLOWED}, lands=["a"])
    seen = rig([_job(f"m{i}", manual=True) for i in range(4)], {})
    assert seen["stopped"] == [] and M._fallback_misses == {}
    assert sum("couldn't be captured" in m for _e, _u, m in seen["said"]) == 4


def test_a_queue_that_filled_meanwhile_discards_the_recording(rig):
    seen = rig([_job("a")], {"a": NOT_ALLOWED}, lands=["a"], kept=False)
    assert seen["stored"] == [] and seen["discarded"] == ["a"] and seen["cues"] == []


def test_a_recording_on_its_way_counts_as_a_queue_slot(rig):
    seen = rig([_job("a")], {"a": NOT_ALLOWED}, lands=["a"])
    assert seen["in_flight"] == [{"u1": 1}] and M._twitch_in_flight == {}


# ── where it is never used ───────────────────────────────────────────────────

def test_a_broadcaster_who_opted_out_is_never_recorded_instead(rig):
    seen = rig([_job("a", channel="optedout")], {"a": NOT_ALLOWED}, lands=["a"])
    assert seen["stopped"] == [("optedout", "u1")] and seen["stored"] == []
    assert dash.twitch_fallback_usable("optedout") is False
    assert dash.twitch_fallback_usable("restricted") is True


def test_an_opt_out_list_that_cannot_be_read_means_no(monkeypatch, rig):
    def boom(ch):
        raise OSError("disk")
    monkeypatch.setattr("src.auth.optout.is_opted_out", boom)
    assert dash.twitch_fallback_usable("restricted") is False


def test_an_expired_sign_in_is_still_the_users_to_fix(rig):
    seen = rig([_job("a")], {"a": TwitchAuthExpiredError("re-login")}, lands=["a"])
    assert seen["stopped"] == [("restricted", "u1")] and seen["stored"] == []
    assert any("Sign out and back in" in m for _e, _u, m in seen["said"])


def test_kick_is_not_touched(rig):
    seen = rig([_job("a", platform="kick")], {"a": RuntimeError("kick capture off")}, lands=["a"])
    assert seen["stored"] == [] and seen["discarded"] == ["a"]


# ── waiting for a whole file ─────────────────────────────────────────────────

def test_the_wait_ends_on_the_cue_or_on_a_file_that_has_settled(rig, tmp_path):
    async def go():
        loop = asyncio.get_running_loop()
        loop.call_later(0.05, dash._note_file_landed, "cue")
        t0 = time.monotonic()
        assert await dash.wait_for_clip_file("cue", 5.0) is True
        assert time.monotonic() - t0 < 1.0
        assert await dash.wait_for_clip_file("cue", 0.0) is True          # already landed
        assert await dash.wait_for_clip_file("nothing", 0.05) is False
        # The cue was lost, but the file is there. Still being written: no.
        clip_files.path_for("fresh").write_bytes(b"x" * 64)
        assert await dash.wait_for_clip_file("fresh", 0.05) is False
        p = clip_files.path_for("settled")
        p.write_bytes(b"x" * 64)
        os.utime(p, (time.time() - 30, time.time() - 30))
        assert await dash.wait_for_clip_file("settled", 0.05) is True
        assert dash._file_waiters == {}
    asyncio.run(go())


# ── the rest of the product treats it as what it is ──────────────────────────

def _clip(cid, **kw):
    base = {"id": cid, "channel": "restricted", "platform": "twitch", "user_id": "u1",
            "status": "pending", "created_at": time.time() - 3600}
    base.update(kw)
    return base


def test_only_a_file_means_kick_or_the_flag():
    assert dash._file_only({"platform": "kick"}) is True
    assert dash._file_only({"platform": "twitch", "file_only": True}) is True
    assert dash._file_only({"platform": "twitch", "twitch_clip_id": "x"}) is False
    assert dash._file_only({"platform": "twitch"}) is False


def test_a_recording_whose_video_is_gone_leaves_the_queue_and_a_twitch_clip_does_not(rig, monkeypatch):
    removed = []

    async def broadcast(event, user_id=None):
        removed.append((event.get("event"), event.get("clip_id")))
    monkeypatch.setattr(dash, "broadcast", broadcast)
    monkeypatch.setattr(dash, "_save_clips", lambda: None)
    monkeypatch.setattr(dash, "_delete_clip_file", lambda c: None)
    monkeypatch.setattr("src.stats.stream_stats.record", lambda *a, **k: None)
    notes = {}
    monkeypatch.setattr("src.auth.users.add_notices", lambda n: notes.update(n))
    monkeypatch.setattr(dash, "_video_gone_noticed", {})
    clip_files.path_for("has").write_bytes(b"x" * 64)
    monkeypatch.setattr(dash, "_clips", {
        "gone": _clip("gone", file_only=True, fallback_reason="not_authorized"),
        "has": _clip("has", file_only=True),
        "real": _clip("real", twitch_clip_id="slug", twitch_url="https://clips.twitch.tv/slug"),
        "new": _clip("new", file_only=True, created_at=time.time() - 30),
        "kept": _clip("kept", file_only=True, status="approved"),
    })
    assert asyncio.run(dash.sweep_videoless_kick_clips()) == 1
    assert sorted(dash._clips) == ["has", "kept", "new", "real"]
    assert ("clip_removed", "gone") in removed
    assert notes["u1"]["title"] == "Some recorded clips were removed from your queue"
    assert "a Twitch moment Twitch would not clip" in notes["u1"]["body"]


def test_it_is_never_looked_up_on_twitch_or_fetched_from_it(rig, monkeypatch):
    asked = []

    async def fetch_existing(slugs):
        asked.extend(slugs)
        return set(slugs)
    monkeypatch.setattr(dash, "_clips", {"rec": _clip("rec", file_only=True, created_at=1.0),
                                         "real": _clip("real", twitch_clip_id="slug", created_at=1.0)})
    assert asyncio.run(dash.sweep_dead_twitch_clips(fetch_existing)) == 0
    assert asked == ["slug"], "a recording was checked against Twitch, where it never was"
    from src.clips import fetch as clip_fetch
    monkeypatch.setattr(clip_fetch, "enabled", lambda: True)
    assert clip_fetch.fetchable(_clip("rec", file_only=True)) is False
    out = dash._clip_out(_clip("rec", file_only=True))
    assert out["file_only"] is True and out["fetchable"] is False and out["has_file"] is False


def test_the_record_is_the_shape_a_kick_clip_has():
    d = ClipProcessor.file_only_record(_job("z"), "title_automod").to_dict()
    assert (d["id"], d["platform"], d["status"], d["file_only"]) == ("z", "twitch", "pending", True)
    assert d["user_id"] == "u1" and d["trigger_score"] == 80.0 and d["game"] == "g"
    assert not (d["twitch_clip_id"] or d["twitch_url"] or d["embed_url"] or d["thumbnail_url"])
    assert ClipMetadata().to_dict()["file_only"] is False            # a normal clip says so too


def test_the_standing_notice_is_told_a_recording_is_being_kept(rig, monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    refusals.record("restricted", refusals.NOT_AUTHORIZED, "u1")
    refusals.record("optedout", refusals.TITLE_AUTOMOD, "u1")
    user = {"id": "u1", "username": "one", "subscription_status": "active"}
    monkeypatch.setattr("src.auth.users.get_by_id", lambda uid: user if uid == "u1" else None)
    c = TestClient(dash.app, base_url="https://testserver")
    c.cookies.set("session", TimestampSigner(dash.settings.dashboard_secret_key).sign(
        base64.b64encode(json.dumps({"auth": True, "user_id": "u1", "username": "one",
                                     "is_admin": False, "subscription_status": "active"}).encode())).decode())
    rows = {r["channel"]: r for r in c.get("/clip-refusals").json()["rows"]}
    assert rows["restricted"]["recording"] is True and rows["optedout"]["recording"] is False
    monkeypatch.setattr(settings, "twitch_file_fallback", False)
    rows = c.get("/clip-refusals").json()["rows"]
    assert len(rows) == 2 and all(r["recording"] is False for r in rows)


# ── what the user reads ──────────────────────────────────────────────────────

def _fn(name):
    i = DASHBOARD_HTML.index(f"function {name}(")
    return DASHBOARD_HTML[i:DASHBOARD_HTML.index("\nfunction ", i + 10)]


def test_the_viewer_plays_the_recording_and_never_sends_anyone_to_twitch_for_it():
    m = _fn("ClipModal")
    assert "const fileOnly = kick || !!clip.file_only;" in m
    assert "const canLinkOut = !embedSrc && !fileSrc && !!outHref && !fileOnly;" in m
    assert "fileSrc && !kick && clip.file_only && <div" in m
    assert "Twitch would not make this clip, so this is the video Highlightz recorded" in m
    assert "Open channel on Twitch" in m
    assert "it exists only as the file Highlightz recorded, and it is not on Twitch either" in m
    assert "gated && !fileOnly && !fileSrc && <div" in m
    gone = m[m.index("clip.file_only && clip.file_state !== 'off'"):]
    assert "it existed only as the file Highlightz recorded" in gone[:600]


def test_the_card_says_its_link_opens_the_channel_not_a_clip():
    c = _fn("RdClip")
    assert "const recorded = !!clip.file_only && clip.platform !== 'kick';" in c
    assert "recorded ? 'Open the channel on Twitch' : 'Open on ' + outName" in c
    assert "{recorded ? 'Channel on Twitch' : <>Open on {outName}</>}" in c
    assert "The clip is a file Highlightz recorded and plays here." in c


def test_the_notice_stops_saying_the_channel_was_stopped_when_it_was_not():
    copy = DASHBOARD_HTML[DASHBOARD_HTML.index("const REFUSAL_COPY"):DASHBOARD_HTML.index("function ReviewScreen(")]
    assert copy.count("rec:") == 3 and copy.count("who:") == 3
    assert "(r.recording && copy.rec) ? ' ' + copy.rec : (copy.who ? ' ' + copy.who : '')" in _fn("ReviewScreen")
    na = copy[copy.index("not_authorized:"):]
    assert "often to followers or subscribers" in na
    assert "We stopped monitoring it" in na[na.index("who:"):na.index("rec:")]
    assert "stopped monitoring" not in na[na.index("rec:"):]
    assert "Twitch clips come back by themselves" in na[na.index("rec:"):]


def test_the_terms_and_the_privacy_policy_say_it_only_while_it_is_true():
    on_tos, on_priv = dash._legal_with_fallback(dash._TOS_TEMPLATE, dash._PRIVACY_TEMPLATE, True)
    off_tos, off_priv = dash._legal_with_fallback(dash._TOS_TEMPLATE, dash._PRIVACY_TEMPLATE, False)
    assert "Where Twitch does not create the clip" in on_tos
    assert "no clip is created on Twitch in that case" in on_tos
    assert "restricted or switched off clipping on their channel" in on_tos
    assert "and on Kick (and on Twitch, where Twitch does not create the clip) the only form the clip takes" in on_priv
    # And the list of what a clip stores names the two fields the record gained.
    assert "we record that it is, and which reason Twitch gave" in on_priv
    assert "that clip is not hosted by Twitch" in on_priv
    assert "which reason Twitch gave" not in off_priv
    assert "Where Twitch does not create the clip" not in off_tos
    assert "and on Kick the only form the clip takes" in off_priv
    for page in (on_tos, on_priv, off_tos, off_priv, dash.TOS_HTML, dash.PRIVACY_HTML):
        assert "FILEONLY" not in page, "a placeholder was served"
    # Served as the switch stands (off in this test run).
    assert "Where Twitch does not create the clip" not in dash.TOS_HTML
    assert "and on Kick the only form the clip takes" in dash.PRIVACY_HTML


def test_no_new_event_was_invented_for_it():
    """Realtime contract: the clip arrives as every clip does (notify_clip_ready)
    and the messages ride `clip_failed` and `refusals_changed`, both handled."""
    src = pathlib.Path("src/main.py").read_text()
    block = src[src.index("# ── When Twitch will not make the clip"):src.index("def _log_clip_error(")]
    assert set(__import__("re").findall(r'"event": "([a-z_]+)"', block)) == {"clip_failed"}
    for event in ("clip_failed", "refusals_changed"):
        assert f"msg.event==='{event}'" in DASHBOARD_HTML
