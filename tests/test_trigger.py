import asyncio
import pytest
from unittest.mock import AsyncMock

from src.trigger.engine import TriggerEngine
from src.trigger.signals import TriggerEvent
from src.trigger.rules import ChannelRules, CHANNEL_OVERRIDES


@pytest.fixture(autouse=True)
def clear_overrides():
    CHANNEL_OVERRIDES.clear()
    yield
    CHANNEL_OVERRIDES.clear()


@pytest.mark.asyncio
async def test_no_trigger_on_empty_chat():
    fired = []
    engine = TriggerEngine("test_channel", on_trigger=lambda e: fired.append(e) or asyncio.sleep(0))
    await engine.evaluate()
    assert len(fired) == 0


@pytest.mark.asyncio
async def test_trigger_fires_on_high_score():
    fired: list[TriggerEvent] = []

    async def capture(event: TriggerEvent):
        fired.append(event)

    # Lower threshold so we can trigger it with synthetic data
    CHANNEL_OVERRIDES["blaster"] = ChannelRules(trigger_threshold=0.01, cooldown_seconds=0)

    engine = TriggerEngine("blaster", on_trigger=capture)

    # Flood with high-energy messages to spike all signals
    for _ in range(200):
        engine.ingest_chat("viewer", "CLIP IT poggers omegalul holy no way LUL")

    await engine.evaluate()
    assert len(fired) == 1
    assert fired[0].score > 0


@pytest.mark.asyncio
async def test_cooldown_prevents_double_trigger():
    fired = []

    async def capture(event):
        fired.append(event)

    CHANNEL_OVERRIDES["chan"] = ChannelRules(trigger_threshold=0.01, cooldown_seconds=9999)
    engine = TriggerEngine("chan", on_trigger=capture)

    for _ in range(200):
        engine.ingest_chat("v", "clip poggers omegalul")

    await engine.evaluate()
    await engine.evaluate()
    assert len(fired) == 1   # second call blocked by cooldown
