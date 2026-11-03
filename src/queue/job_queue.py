"""
Redis-backed clip job queue.
Producers (StreamWorker) push ClipJob dicts; workers pop and process them.
"""

import json
import re as _re
import time
import uuid
import asyncio
from redis.asyncio import Redis, from_url as redis_from_url
import structlog
from dataclasses import dataclass, field, asdict

from config.settings import settings

log = structlog.get_logger(__name__)

QUEUE_KEY = "superclipbot:clip_jobs"
# Studio's clips (plans.uid_has_priority), cut ahead of everyone else's
# (2026-11-03). A second list rather than LPUSH onto the first, so priority
# jobs stay first-in-first-out among themselves; BLPOP takes from the first
# non-empty key in the order given, so this one is always drained first.
PRIORITY_KEY = "superclipbot:clip_jobs:priority"
PROCESSING_KEY = "superclipbot:processing"

_CHANNEL_RE_Q = _re.compile(r'^[A-Za-z0-9_\-]{1,64}$')


@dataclass
class ClipJob:
    channel: str
    platform: str
    trigger_score: float
    trigger_signals: list[dict]
    chat_snapshot: list[str]
    stream_title: str = ""
    game: str = ""
    pre_roll: int = 30
    # post_roll times the Twitch API call; clip_post_roll is how much of the
    # aftermath our own cut keeps. See TriggerEvent for why they are separate.
    # 0 means "use post_roll" — the manual force-clip path sets only post_roll.
    post_roll: int = 10
    clip_post_roll: int = 0
    clip_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    virality_score: float = 0.0
    clip_title: str = ""
    user_id: str = ""
    # Captured from a channel flagged mature — see StreamInfo.is_mature. Rides
    # on the job rather than being looked up later, because by the time the
    # processor runs the stream may have ended and the flag would be gone.
    age_restricted: bool = False
    # Wall-clock time the moment was captured (enqueue time). The clip processor
    # drops jobs older than the Twitch capture window — see MAX_CLIP_JOB_AGE_SECS
    # in src/main.py — so a backlog can never drain stale moments into
    # "Channel offline." 404s.
    created_at: float = field(default_factory=lambda: time.time())

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "ClipJob":
        data = json.loads(raw)
        # Jobs enqueued before created_at existed have no timestamp. Treat them as
        # epoch-old so the processor's staleness check drops them — on the first
        # restart after deploy this clears any pre-existing backlog instead of
        # replaying hours-old moments against now-offline channels.
        data.setdefault("created_at", 0.0)
        return cls(**data)


class JobQueue:
    def __init__(self, priority_of=None) -> None:
        self._redis: Redis | None = None
        # user id -> bool, injected by main.py (plans.uid_has_priority), so
        # this module needs to know nothing about plans or accounts.
        self._priority_of = priority_of

    async def connect(self) -> None:
        self._redis = await redis_from_url(settings.redis_url, decode_responses=True)
        from urllib.parse import urlparse
        parsed = urlparse(settings.redis_url)
        log.info("job_queue_connected", host=parsed.hostname, port=parsed.port, db=parsed.path.lstrip("/"))

    async def close(self) -> None:
        if self._redis:
            await self._redis.close()

    def _require_redis(self) -> None:
        if self._redis is None:
            raise RuntimeError("JobQueue.connect() has not been called")

    async def push(self, job: ClipJob) -> None:
        self._require_redis()
        key = QUEUE_KEY
        if self._priority_of and job.user_id:
            try:
                if self._priority_of(job.user_id):
                    key = PRIORITY_KEY
            except Exception:                 # never lose a job over a lookup
                key = QUEUE_KEY
        await self._redis.rpush(key, job.to_json())
        log.info("job_enqueued", clip_id=job.clip_id, channel=job.channel,
                 priority=key == PRIORITY_KEY)

    async def pop(self, timeout: int = 5) -> ClipJob | None:
        self._require_redis()
        result = await self._redis.blpop([PRIORITY_KEY, QUEUE_KEY], timeout=timeout)
        if result is None:
            return None
        _, raw = result
        try:
            job = ClipJob.from_json(raw)
        except Exception as exc:
            log.warning("job_queue_invalid_job", error=str(exc))
            return None
        if not _CHANNEL_RE_Q.fullmatch(job.channel):
            log.warning("job_queue_bad_channel", channel=job.channel)
            return None
        if job.platform not in ("twitch", "kick"):
            log.warning("job_queue_bad_platform", platform=job.platform)
            return None
        # trigger_score is on a 0–100 scale (see TriggerEngine._compute_score)
        if not isinstance(job.trigger_score, (int, float)) or not (0.0 <= job.trigger_score <= 100.0):
            log.warning("job_queue_bad_trigger_score", score=job.trigger_score)
            return None
        if not (0 <= job.pre_roll <= 300):
            log.warning("job_queue_bad_pre_roll", pre_roll=job.pre_roll)
            return None
        if not (0 <= job.post_roll <= 120):
            log.warning("job_queue_bad_post_roll", post_roll=job.post_roll)
            return None
        # Same bound as post_roll. The cut reads this one, and a wild value
        # would ask the recorder for a window its buffer cannot hold.
        if not (0 <= job.clip_post_roll <= 120):
            log.warning("job_queue_bad_clip_post_roll", clip_post_roll=job.clip_post_roll)
            return None
        if not isinstance(job.chat_snapshot, list) or not all(isinstance(m, str) for m in job.chat_snapshot):
            log.warning("job_queue_bad_chat_snapshot")
            return None
        for field in ("stream_title", "game", "clip_title"):
            val = getattr(job, field, "")
            if len(val) > 500:
                log.warning("job_queue_field_too_long", field=field)
                return None
        return job

    async def queue_length(self) -> int:
        self._require_redis()
        return (await self._redis.llen(QUEUE_KEY)) + (await self._redis.llen(PRIORITY_KEY))
