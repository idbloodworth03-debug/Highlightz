"""What the public counter counts — and what it must never count.

The landing page renders this number under the words "clips captured and
counting", and render_landing() also writes it into the JSON-LD
interactionStatistic as userInteractionCount. Both are claims made to people
who have not bought anything yet, and one of them is a machine-readable claim
to search engines. So the counter has exactly one honest definition, the one
in its own docstring: every clip the system has ever CAPTURED, regardless of
what happened to it afterwards.

That cuts both ways, and both directions are pinned here.

UNDERCOUNT (fixed). notify_clip_ready runs AFTER processor.process(job) has
already created the clip on Twitch — see src/main.py:204-205. The queue-full
branch therefore discards a clip that genuinely exists on the user's Twitch
account, dropped only because their review queue filled in the race between
the processor's pending_room() pre-check and this second check. That is a
capture by any reading, and it was not being counted.

OVERCOUNT (must never happen). A moment that never became a Twitch clip is not
a capture, and neither is the same clip counted twice. The tests below fix
those at zero so the number can only ever be raised by something real.
"""

import asyncio
import inspect

import pytest

from src.dashboard import api
from src.stats import stream_stats as ss


@pytest.fixture(autouse=True)
def counted(tmp_path, monkeypatch):
    """Everything external stubbed, with the counter REAL and observable.

    The sibling suite (test_queue_full_drops_new.py) stubs increment_clip_counter
    to a no-op because it is asking a different question. Here the increments are
    the subject, so they are recorded instead of discarded.
    """
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)
    monkeypatch.setattr(api, "_CLIP_COUNTER_FILE", tmp_path / "clip_counter.json")
    monkeypatch.setattr(api, "_clip_counter", 0)

    async def _bcast(payload, user_id=None):
        pass
    monkeypatch.setattr(api, "broadcast", _bcast)

    from src.auth import users as us
    monkeypatch.setattr(us, "get_by_id", lambda uid: {
        "id": uid, "subscription_status": "none", "grandfathered": True})  # free: 15
    api._clips.clear()
    yield
    api._clips.clear()


def _fill(n, uid="u1"):
    for i in range(n):
        api._clips[f"p{i}"] = {"id": f"p{i}", "user_id": uid, "channel": "aceu",
                               "status": "pending", "created_at": 1000.0 + i}


def _incoming(cid="new", created_at=9999.0):
    return {"id": cid, "user_id": "u1", "channel": "aceu", "status": "pending",
            "created_at": created_at, "platform": "twitch",
            "twitch_url": "https://clips.twitch.tv/N"}


# ── the undercount that was fixed ────────────────────────────────────────────

def test_a_clip_dropped_for_a_full_queue_still_counts_as_captured():
    """THE fix. The clip exists on Twitch; only our copy was discarded."""
    _fill(15)
    before = api.get_clip_counter()
    asyncio.run(api.notify_clip_ready(_incoming()))
    assert "new" not in api._clips, "precondition: the clip should have been dropped"
    assert api.get_clip_counter() == before + 1, \
        "a clip that was created on Twitch was left out of the capture count"


def test_the_ordinary_accepted_clip_still_counts_exactly_once():
    """The partner check: raising the dropped path must not double the normal
    one, which is where all but a handful of the number comes from."""
    _fill(14)
    before = api.get_clip_counter()
    asyncio.run(api.notify_clip_ready(_incoming()))
    assert "new" in api._clips, "precondition: this clip should have been kept"
    assert api.get_clip_counter() == before + 1


# ── the overcounts that must stay impossible ─────────────────────────────────

def test_a_deduplicated_clip_does_not_count_again():
    """Same moment, already counted when it was first stored. Counting it here
    would inflate the public number with clips that do not exist."""
    _fill(1)
    api._clips["p0"]["created_at"] = 9999.0      # inside the dedup window
    before = api.get_clip_counter()
    asyncio.run(api.notify_clip_ready(_incoming(cid="dupe", created_at=9999.0)))
    assert api.get_clip_counter() == before, \
        "a duplicate of an existing clip was counted a second time"


def test_a_missed_moment_is_not_a_capture():
    """stream_stats separates CAUGHT from MISSED for the streamer-facing keep
    rate. The public counter has to honour the same line: a moment nothing was
    ever made from is not a captured clip."""
    _fill(15)
    asyncio.run(api.notify_clip_ready(_incoming()))
    row = ss.for_channel("u1", "aceu")
    assert row["missed"] == 1 and row["caught"] == 0, \
        "the queue-full drop stopped being recorded as a miss"


def test_the_counter_is_only_ever_raised_from_a_real_capture_site():
    """A guard on where increment_clip_counter may be called from.

    The counter's honesty is a property of its CALL SITES, not of its own code,
    so no test of the function can protect it. There are exactly three
    legitimate sites, and all three are a clip that demonstrably exists:

      1. a live clip Twitch created and we stored
      2. a live clip Twitch created that we then discarded for queue capacity
      3. a VOD moment that was found

    This fails if a fourth appears — in particular one counting triggers,
    cooldown-suppressed moments, stale-dropped jobs, processor errors or
    retries, none of which produced a clip.
    """
    import pathlib
    src = pathlib.Path("src/dashboard/api.py").read_text()
    call_lines = [l.strip() for l in src.splitlines()
                  if "increment_clip_counter(" in l
                  and not l.strip().startswith("def ")
                  and "increment_clip_counter(n" not in l]
    assert len(call_lines) == 3, (
        f"expected exactly 3 increment sites (live clip stored, live clip "
        f"dropped for capacity, VOD moment), found {len(call_lines)}: "
        f"{call_lines}. A new one must be a clip that actually exists.")


def test_the_landing_page_still_describes_it_as_captured():
    """If the wording ever softens to something vaguer, the tests above stop
    matching the promise the page actually makes. Pinning the words keeps the
    definition and the claim in the same place."""
    import pathlib
    src = pathlib.Path("src/dashboard/api.py").read_text()
    assert "clips captured and counting" in src, \
        "the ticker's wording changed — recheck what the counter is allowed to count"


def test_the_json_ld_count_is_the_same_number_the_tile_shows():
    """One number, two audiences. A tile and a structured-data claim that can
    disagree is the shape a misleading stat takes."""
    src = inspect.getsource(api.render_landing)
    assert "userInteractionCount" in src
    assert src.count("total") >= 3, "render_landing stopped using one shared total"
    # Both substitutions must be fed by the same `total`, not recomputed.
    assert 'f\'<span id="lp-count" data-count="{total}"' in src or \
           'data-count="{total}"' in src
    assert '"userInteractionCount": {total}' in src
