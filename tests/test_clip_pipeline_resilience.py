"""Three ways the clipping pipeline failed badly instead of failing gracefully.

Found by driving the real pipeline end to end and then breaking it on purpose
(scratchpad/pipeline_audit.py). The happy path was fine — a hype burst becomes a
clip in the review queue, 19 checks, all green. These are the edges around it.

  1. Redis unreachable spun the processor loop at ~6,500 attempts a second,
     writing a full traceback each time. On a 1vCPU box that is CPU starvation
     and a disk-filling log, triggered by a Redis restart.
  2. A job dropped for being stale told the user nothing. Their highlight went
     by, no clip appeared, and the only trace was a log line they cannot see.
  3. An expired Twitch token produced "it'll try again on the next moment",
     which is false — it never succeeds until they re-authorise.
"""

import asyncio
import time

import pytest


@pytest.fixture
def loop_env(monkeypatch):
    import src.main as M
    from src.dashboard import api as dash

    calls = {"pops": 0}
    sent, missed, stopped = [], [], []

    async def _bc(msg, **kw): sent.append((msg.get("event"), msg.get("message", ""), kw.get("user_id")))
    async def _missed(uid, ch, reason="queue_full"): missed.append((uid, ch, reason))
    async def _stop(ch, uid): stopped.append((ch, uid))
    monkeypatch.setattr(dash, "broadcast", _bc)
    monkeypatch.setattr(dash, "notify_clip_missed", _missed)
    monkeypatch.setattr(dash, "stop_stream_internal", _stop)
    monkeypatch.setattr(dash, "pending_room", lambda uid: (0, 200))

    async def run(queue, processor=None, seconds=0.5):
        monkeypatch.setattr(M, "_queue", queue)
        if processor is not None:
            monkeypatch.setattr(M, "ClipProcessor", lambda *a, **k: processor)
        task = asyncio.create_task(M.run_clip_processor())
        await asyncio.sleep(seconds)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    return {"run": run, "calls": calls, "sent": sent, "missed": missed,
            "stopped": stopped, "M": M}


# ── 1. Redis down must not become a CPU fire ────────────────────────────────

def test_an_unreachable_queue_backs_off_instead_of_spinning(loop_env):
    """Measured before the fix: 13,031 attempts in 2 seconds, each logging a
    traceback. The loop must pause, not retry as fast as the event loop allows."""
    calls = {"n": 0}

    class Dead:
        async def pop(self, timeout=5):
            calls["n"] += 1
            await asyncio.sleep(0)          # redis-py yields on the socket attempt
            raise ConnectionError("Error 111 connecting to localhost:6379")

    asyncio.run(loop_env["run"](Dead(), seconds=3.0))
    assert calls["n"] <= 6, \
        f"the processor retried {calls['n']} times in 3s — it is spinning again"
    assert calls["n"] >= 1, "the processor stopped trying to reach the queue at all"


def test_the_backoff_escalates_rather_than_hammering_at_a_fixed_rate(loop_env):
    import src.main as M
    assert M._QUEUE_RETRY_START >= 1.0, "the first retry is too eager"
    assert M._QUEUE_RETRY_MAX >= 10.0, "the backoff never gets far enough apart"

    gaps = []
    class Dead:
        async def pop(self, timeout=5):
            gaps.append(time.monotonic())
            await asyncio.sleep(0)
            raise ConnectionError("down")

    asyncio.run(loop_env["run"](Dead(), seconds=4.0))
    deltas = [round(b - a, 1) for a, b in zip(gaps, gaps[1:])]
    assert deltas == sorted(deltas), f"the backoff is not escalating: {deltas}"


def test_a_healthy_queue_is_not_slowed_down(loop_env):
    """The backoff must reset the moment the queue answers, or one blip would
    permanently throttle clip creation to a job every 30 seconds."""
    calls = {"n": 0}

    class Flaky:
        async def pop(self, timeout=5):
            calls["n"] += 1
            await asyncio.sleep(0)
            if calls["n"] == 1:
                raise ConnectionError("down")
            return None                      # recovered: idle, no job waiting

    # One failure costs one _QUEUE_RETRY_START pause; the rest of the window is
    # free polling. If the backoff failed to reset, the second pause would be
    # 2s and the third 4s, and the count would stay in single figures.
    asyncio.run(loop_env["run"](Flaky(), seconds=2.5))
    assert calls["n"] > 20, \
        f"only {calls['n']} polls after recovery — the backoff never reset"


def test_a_second_outage_starts_from_a_short_pause_again(loop_env):
    """The reset has to be observable, and a SINGLE outage cannot show it: with
    one failure, "reset to 1s" and "never escalated past 1s" look identical.
    Two episodes with a recovery between them is what distinguishes them —
    without the reset the second outage starts where the first left off and
    keeps doubling, so a flaky Redis walks the pause up to the 30s cap and stays
    there while the queue is perfectly healthy."""
    marks = []

    class TwoOutages:
        def __init__(self): self.n = 0
        async def pop(self, timeout=5):
            self.n += 1
            marks.append(time.monotonic())
            await asyncio.sleep(0)
            # fail, recover, fail again
            if self.n in (1, 2, 4):
                raise ConnectionError("down")
            return None

    asyncio.run(loop_env["run"](TwoOutages(), seconds=6.0))
    assert len(marks) >= 5, f"the loop stalled entirely: {len(marks)} polls"
    # Gap before the SECOND outage's retry (marks[4] -> marks[5]) must be the
    # short starting pause, not a continuation of the first outage's escalation.
    gap_after_second_outage = marks[4] - marks[3]
    import src.main as M
    assert gap_after_second_outage < M._QUEUE_RETRY_START * 3.5, \
        (f"the second outage paused {gap_after_second_outage:.1f}s — the backoff "
         f"never reset after recovery")


def test_a_failing_job_does_not_trigger_the_queue_backoff(loop_env):
    """The backoff is for an unreachable QUEUE. A clip that fails to capture is
    a different thing, and pausing 30s on it would stall every other channel."""
    import src.main as M
    src = __import__("inspect").getsource(M.run_clip_processor)
    i = src.index("queue_backoff = min(")
    guard = src[max(0, i - 400):i]
    assert "if job is None:" in guard, \
        "the backoff is applied to job failures, not just queue failures"


# ── 2. A dropped moment must reach the user ─────────────────────────────────

def test_a_stale_job_tells_the_user_it_was_dropped(loop_env):
    """Previously invisible: the highlight went by, no clip appeared, and the
    only record was a log line the user cannot read."""
    from src.queue.job_queue import ClipJob
    old = ClipJob(clip_id="c", channel="lacy", platform="twitch", user_id="u1",
                  trigger_score=80.0, trigger_signals={}, chat_snapshot=[],
                  stream_title="", game="", virality_score=0.0, clip_title="",
                  post_roll=0)
    old.created_at = time.time() - 500       # long past the staleness cutoff

    served = {"done": False}
    class Q:
        async def pop(self, timeout=5):
            await asyncio.sleep(0)
            if served["done"]:
                return None
            served["done"] = True
            return old

    asyncio.run(loop_env["run"](Q(), seconds=0.5))
    assert loop_env["missed"], "a stale drop notified nobody"
    uid, ch, reason = loop_env["missed"][0]
    assert (uid, ch) == ("u1", "lacy")
    assert reason == "backlog", \
        "a backlog was reported as queue_full — that tells them to upgrade for nothing"


def test_the_frontend_separates_a_backlog_from_a_full_queue():
    """notify_clip_missed drives an upgrade banner. A bigger queue does not fix
    a backlog, so showing that prompt would be selling against a real problem."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    i = html.index("msg.event==='clip_missed'")
    block = html[i:i + 900]
    assert "msg.reason==='backlog'" in block, "both causes render identically"
    lines = [l for l in block.splitlines() if not l.strip().startswith("//")]
    branch = "\n".join(lines)
    before_else = branch[:branch.index("} else {")]
    assert "setLostClips" not in before_else, \
        "the upgrade banner still fires for a backlog"


# ── 3. A dead token is permanent, and must be said so ───────────────────────

def test_an_expired_token_raises_something_the_loop_can_recognise():
    from src.processor.clip_processor import TwitchAuthExpiredError
    assert issubclass(TwitchAuthExpiredError, RuntimeError)


def test_the_processor_itself_raises_the_specific_error(monkeypatch):
    """Drives the REAL _process_twitch with no token. Without this the tests
    above only prove the loop handles the exception if something raises it —
    they never check that anything does, so downgrading it back to a bare
    RuntimeError would pass every one of them."""
    from src.processor.clip_processor import ClipProcessor, TwitchAuthExpiredError
    from src.auth import users as user_store
    from src.queue.job_queue import ClipJob

    async def _no_token(uid): return None
    monkeypatch.setattr(user_store, "get_valid_twitch_token", _no_token)

    job = ClipJob(clip_id="c", channel="lacy", platform="twitch", user_id="u1",
                  trigger_score=80.0, trigger_signals={}, chat_snapshot=[],
                  stream_title="", game="", virality_score=0.0, clip_title="",
                  post_roll=0)
    with pytest.raises(TwitchAuthExpiredError):
        asyncio.run(ClipProcessor().process(job))


def test_an_expired_token_stops_the_stream_and_says_to_reconnect(loop_env):
    """It cannot succeed until they re-authorise, so the generic "it'll try
    again on the next moment" was a lie that left them waiting."""
    from src.processor.clip_processor import TwitchAuthExpiredError
    from src.queue.job_queue import ClipJob

    job = ClipJob(clip_id="c", channel="lacy", platform="twitch", user_id="u1",
                  trigger_score=80.0, trigger_signals={}, chat_snapshot=[],
                  stream_title="", game="", virality_score=0.0, clip_title="",
                  post_roll=0)

    class Proc:
        async def process(self, j): raise TwitchAuthExpiredError("no token")

    served = {"done": False}
    class Q:
        async def pop(self, timeout=5):
            await asyncio.sleep(0)
            if served["done"]:
                return None
            served["done"] = True
            return job

    asyncio.run(loop_env["run"](Q(), processor=Proc(), seconds=0.5))
    assert ("lacy", "u1") in loop_env["stopped"], \
        "monitoring kept running against a connection that cannot clip"
    msgs = [m for ev, m, uid in loop_env["sent"] if ev == "clip_failed"]
    assert msgs, "the user was never told"
    assert "try again" not in msgs[0].lower(), \
        "still promising a retry that can never succeed"
    assert "expired" in msgs[0].lower() or "reconnect" in msgs[0].lower()


def test_a_genuinely_transient_failure_still_promises_a_retry(loop_env):
    """The partner test. A capture that failed for a normal reason SHOULD say
    it will try again, or this fix would make every hiccup look terminal."""
    from src.queue.job_queue import ClipJob
    job = ClipJob(clip_id="c", channel="lacy", platform="twitch", user_id="u1",
                  trigger_score=80.0, trigger_signals={}, chat_snapshot=[],
                  stream_title="", game="", virality_score=0.0, clip_title="",
                  post_roll=0)

    class Proc:
        async def process(self, j): raise RuntimeError("capture failed")

    served = {"done": False}
    class Q:
        async def pop(self, timeout=5):
            await asyncio.sleep(0)
            if served["done"]:
                return None
            served["done"] = True
            return job

    asyncio.run(loop_env["run"](Q(), processor=Proc(), seconds=0.5))
    assert loop_env["stopped"] == [], "a transient failure stopped the stream"
    msgs = [m for ev, m, uid in loop_env["sent"] if ev == "clip_failed"]
    assert msgs and "try again" in msgs[0].lower()
