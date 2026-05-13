"""
Clip processor worker process.

Pops ClipJob items from the Redis queue and processes them using ClipProcessor.
Run as many instances as you have CPU/bandwidth to spare.
"""

import asyncio
import structlog

from config.settings import settings
from src.queue.job_queue import JobQueue
from src.processor.clip_processor import ClipProcessor
from src.output.storage import build_storage
from src.dashboard.api import notify_clip_ready

log = structlog.get_logger(__name__)

# Shared buffer registry — populated when workers run in the same process as main.
# In a multi-process deployment, workers communicate with the buffer host via RPC.
SHARED_BUFFERS: dict = {}


async def run_worker() -> None:
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ]
    )

    queue = JobQueue()
    await queue.connect()

    storage = build_storage()
    processor = ClipProcessor(storage=storage, buffers=SHARED_BUFFERS)

    log.info("clip_worker_started")

    while True:
        job = await queue.pop(timeout=5)
        if job is None:
            continue

        log.info("clip_job_received", clip_id=job.clip_id, channel=job.channel)
        try:
            meta = await processor.process(job)
            await notify_clip_ready(meta.to_dict())
        except Exception as exc:
            log.error("clip_job_failed", clip_id=job.clip_id, error=str(exc))


if __name__ == "__main__":
    asyncio.run(run_worker())
