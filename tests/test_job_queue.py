"""
ClipJob staleness contract — the data side of the stale-backlog fix.

The clip processor drops jobs older than MAX_CLIP_JOB_AGE_SECS so a queue
backlog can never replay stale moments into "Channel offline." 404s. That only
works if (a) fresh jobs carry a recent created_at and (b) legacy jobs enqueued
before created_at existed are treated as ancient (so the first restart after
deploy clears the old backlog).
"""

import json
import time

from src.queue.job_queue import ClipJob
from src.main import MAX_CLIP_JOB_AGE_SECS


def _job(**kw) -> ClipJob:
    base = dict(channel="caseoh_", platform="twitch", trigger_score=85.0,
                trigger_signals=[], chat_snapshot=[])
    base.update(kw)
    return ClipJob(**base)


def test_fresh_job_is_recent_and_survives_cutoff():
    job = _job()
    age = time.time() - job.created_at
    assert age < MAX_CLIP_JOB_AGE_SECS, "a just-created job must not be stale"


def test_created_at_survives_json_roundtrip():
    job = _job()
    restored = ClipJob.from_json(job.to_json())
    assert restored.created_at == job.created_at


def test_legacy_job_without_timestamp_is_ancient():
    """A job serialized before created_at existed → epoch-old → dropped."""
    legacy = json.dumps({
        "channel": "caseoh_", "platform": "twitch", "trigger_score": 90.0,
        "trigger_signals": [], "chat_snapshot": [],
    })
    restored = ClipJob.from_json(legacy)
    age = time.time() - restored.created_at
    assert age > MAX_CLIP_JOB_AGE_SECS, "legacy job must exceed the staleness cutoff"
