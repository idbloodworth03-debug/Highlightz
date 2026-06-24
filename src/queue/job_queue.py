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
    post_roll: int = 10
    clip_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    virality_score: float = 0.0
    clip_title: str = ""
    user_id: str = ""
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
    def __init__(self) -> None:
        self._redis: Redis | None = None

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
        await self._redis.rpush(QUEUE_KEY, job.to_json())
        log.info("job_enqueued", clip_id=job.clip_id, channel=job.channel)

    async def pop(self, timeout: int = 5) -> ClipJob | None:
        self._require_redis()
        result = await self._redis.blpop(QUEUE_KEY, timeout=timeout)
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
        return await self._redis.llen(QUEUE_KEY)
