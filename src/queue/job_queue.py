"""
Redis-backed clip job queue.
Producers (StreamWorker) push ClipJob dicts; workers pop and process them.
"""

import json
import uuid
import asyncio
from redis.asyncio import Redis, from_url as redis_from_url
import structlog
from dataclasses import dataclass, field, asdict

from config.settings import settings

log = structlog.get_logger(__name__)

QUEUE_KEY = "superclipbot:clip_jobs"
PROCESSING_KEY = "superclipbot:processing"


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

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, raw: str) -> "ClipJob":
        return cls(**json.loads(raw))


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
        return ClipJob.from_json(raw)

    async def queue_length(self) -> int:
        self._require_redis()
        return await self._redis.llen(QUEUE_KEY)
