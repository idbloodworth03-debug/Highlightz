"""The clip's tail is not the API's tail, and they used to be one field.

THE BUG THIS FILE EXISTS TO PREVENT COMING BACK. `post_roll` meant two
unrelated things:

  * how long to WAIT before calling Twitch's Create Clip. It has to stay
    small, because Twitch captures the window ENDING at the call — waiting
    longer pushes the moment off the front of what Twitch keeps. TAIL_SECS,
    4 seconds.
  * how much of the AFTERMATH our own cut keeps in the file. The presets ask
    for 22-32s of it by content type.

The trigger passed TAIL_SECS as both, so every captured clip ended four
seconds after the moment. Measured on production 2026-09-19 across 104 Kick
clips: median 24s against presets asking 35-66s, minimum exactly 14s —
which is `variety`, whose pre-roll is 10. The arithmetic matched the bug on
the nose at both ends.
"""

import inspect

import pytest

from src.queue.job_queue import ClipJob
from src.trigger.rules import PRESETS
from src.trigger.signals import TriggerEvent


# ── the two fields are actually two fields ───────────────────────────────────

def test_the_trigger_sends_the_presets_tail_for_the_cut_and_the_small_one_for_twitch():
    from src.trigger import engine
    src = inspect.getsource(engine.TriggerEngine._monitor_and_fire)
    assert "post_roll=TAIL_SECS," in src, "the Twitch call lost its small tail"
    assert "clip_post_roll=rules.post_roll," in src, \
        "the cut is back on the API's 4-second tail"


def test_every_preset_asks_for_a_real_tail():
    """If a preset's post_roll were small, splitting the fields would buy
    nothing — the point is that they ask for 20s+ and were being ignored."""
    for name, r in PRESETS.items():
        assert r.post_roll >= 20, f"{name} asks for only {r.post_roll}s of aftermath"


@pytest.mark.parametrize("name,rules", sorted(PRESETS.items()))
def test_each_preset_now_produces_the_length_it_asks_for(name, rules):
    """The whole clip: pre-roll (plus up to 2s of chat-lag correction) and
    the preset's own tail. Under the bug this was pre_roll + 4."""
    for lag in (0, 2):
        ev = TriggerEvent(channel="c", score=90.0, signals=[],
                          pre_roll=rules.pre_roll + lag, post_roll=4,
                          clip_post_roll=rules.post_roll)
        cut = ev.pre_roll + (ev.clip_post_roll or ev.post_roll)
        assert cut == rules.pre_roll + lag + rules.post_roll
        assert cut >= 35, f"{name} still cuts only {cut}s"


def test_the_production_numbers_that_found_it_can_no_longer_happen():
    """variety 10+4=14s was the observed minimum and 20+4=24s the median."""
    for preset, was in (("variety", 14), ("small", 24)):
        r = PRESETS[preset]
        assert r.pre_roll + r.post_roll > was + 10


# ── the fallback, so nothing that only sets post_roll breaks ─────────────────

def test_zero_means_fall_back_to_post_roll():
    """The manual force-clip path (main.py) sets pre_roll=60, post_roll=10 and
    knows nothing about the new field. It must keep cutting 70s."""
    job = ClipJob(channel="c", platform="kick", trigger_score=100.0,
                  trigger_signals=[], chat_snapshot=[], pre_roll=60, post_roll=10)
    assert job.clip_post_roll == 0
    assert job.pre_roll + (job.clip_post_roll or job.post_roll) == 70


def test_the_worker_uses_the_clip_tail_and_falls_back():
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker.StreamWorker._on_trigger)
    assert "event.clip_post_roll or event.post_roll" in src, \
        "the cut is back on the Twitch API's tail"
    assert "clip_post_roll=event.clip_post_roll," in src, "the job loses the cut's tail"


def test_the_queue_bounds_the_new_field_like_the_old_one():
    import inspect as _i
    from src.queue import job_queue
    src = _i.getsource(job_queue)
    assert "0 <= job.clip_post_roll <= 120" in src


# ── and the processor does not pay for it ────────────────────────────────────

def test_kick_does_not_block_the_serial_processor_on_the_tail():
    """run_clip_processor handles jobs one at a time and drops anything 90s
    old. Sleeping out a 32s tail here would age every queued moment behind it
    and report the third Kick clip in a row as a missed one."""
    from src.processor.clip_processor import ClipProcessor
    src = inspect.getsource(ClipProcessor._process_kick)
    assert "asyncio.sleep" not in src, \
        "the Kick path sleeps again — it blocks every other job while it does"
    assert "job.clip_post_roll or job.post_roll" in src
    assert "job.pre_roll + tail" in src, "the record's duration is not the cut's"


def test_twitch_still_calls_create_clip_promptly():
    """The other half. Twitch captures the window ENDING at the call, so this
    wait must stay the small one — lengthening it moves the moment off the
    front of the clip."""
    from src.processor.clip_processor import ClipProcessor
    src = inspect.getsource(ClipProcessor._process_twitch)
    assert "min(job.post_roll, 55)" in src
    assert "clip_post_roll" not in src, \
        "the Twitch API call now waits out the cut's tail and will miss the moment"


def test_the_longest_window_still_fits_the_capture_buffer():
    """A cut cannot reach further back than the buffer holds. The longest
    preset window must leave room, or the fix trades short clips for none."""
    from config.settings import Settings
    buf = Settings().clip_capture_buffer_s
    longest = max(r.pre_roll + 2 + r.post_roll for r in PRESETS.values())
    assert longest < buf, f"{longest}s window against a {buf}s buffer"
