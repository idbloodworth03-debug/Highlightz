"""
The dashboard's engine-heartbeat chips are fed entirely by the underscore
extras in the score_update breakdown. If these fields disappear, the heartbeat
silently shows nothing — so lock the contract here.
"""

import asyncio

from src.trigger.engine import TriggerEngine
from src.profiles.profile import StreamerProfile


def test_score_breakdown_carries_heartbeat_fields():
    captured = {}

    async def on_score(channel, score, breakdown):
        captured.update(breakdown)

    async def on_trigger(_):
        pass

    async def scenario():
        eng = TriggerEngine("x", on_trigger=on_trigger, on_score=on_score,
                            profile=StreamerProfile(channel="x"))
        # No chat ever ingested → freshness sentinel must be -1, not 0
        await eng.evaluate()
        assert captured["_last_chat_s"] == -1
        assert captured["_chat_vps"] == 0
        assert captured["_threshold"] == 60.0        # profile seed threshold

        # After chat arrives, freshness becomes a small non-negative age
        eng.ingest_chat("viewer1", "hello there")
        eng.ingest_chat("viewer2", "hello again")
        captured.clear()
        await eng.evaluate()
        assert 0 <= captured["_last_chat_s"] <= 2
        assert "_chat_base_vps" in captured

    asyncio.run(scenario())
