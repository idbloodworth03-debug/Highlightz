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


async def _noop(event):
    pass


def _hot_engine(channel, rules) -> TriggerEngine:
    """Engine whose actual clip-firing is stubbed.

    A real trigger no longer calls on_trigger synchronously — evaluate() spawns
    the detached _monitor_and_fire task, which waits for the moment to dull
    (up to 52s) before firing. For decision-logic tests we only care *whether*
    the engine chose to fire, so we replace _monitor_and_fire with an AsyncMock
    and assert its call count. (It's invoked synchronously inside evaluate via
    create_task, so the call is recorded immediately.)
    """
    CHANNEL_OVERRIDES[channel] = rules
    engine = TriggerEngine(channel, on_trigger=_noop)
    engine._monitor_and_fire = AsyncMock()
    return engine


def _flood(engine, n=200, msg="CLIP IT poggers omegalul holy no way LUL"):
    for _ in range(n):
        engine.ingest_chat("viewer", msg)


@pytest.mark.asyncio
async def test_no_trigger_on_empty_chat():
    # Default threshold (60): an idle channel's baseline score stays well under it.
    engine = _hot_engine("quiet", ChannelRules(trigger_threshold=60.0, cooldown_seconds=0))
    await engine.evaluate()
    engine._monitor_and_fire.assert_not_called()


@pytest.mark.asyncio
async def test_trigger_fires_on_high_score():
    engine = _hot_engine("blaster", ChannelRules(trigger_threshold=0.01, cooldown_seconds=0))
    _flood(engine)
    await engine.evaluate()
    engine._monitor_and_fire.assert_called_once()


@pytest.mark.asyncio
async def test_cooldown_prevents_double_trigger():
    # emergency_threshold=101 disables the override so the 2nd call is purely
    # cooldown-suppressed.
    engine = _hot_engine("chan", ChannelRules(trigger_threshold=0.01,
                                              cooldown_seconds=9999,
                                              emergency_threshold=101))
    _flood(engine)
    await engine.evaluate()   # fires
    await engine.evaluate()   # in cooldown, override disabled → suppressed
    assert engine._monitor_and_fire.call_count == 1


@pytest.mark.asyncio
async def test_emergency_override_respects_its_own_cooldown():
    """Regression guard for the queue-flood fix: a channel parked above
    emergency_threshold must NOT re-fire on every tick. The override may break
    the normal cooldown, but only once per emergency_cooldown_seconds."""
    engine = _hot_engine("hot", ChannelRules(trigger_threshold=0.01,
                                             cooldown_seconds=9999,
                                             emergency_threshold=0.01,
                                             emergency_cooldown_seconds=30))
    _flood(engine)

    await engine.evaluate()                       # fire #1 (not in cooldown)
    assert engine._monitor_and_fire.call_count == 1

    await engine.evaluate()                       # since_last ~0s < 30s → suppressed
    assert engine._monitor_and_fire.call_count == 1

    # Age the last trigger past the emergency floor but keep it inside the normal
    # 9999s cooldown — the override should now be allowed exactly once more.
    engine._last_trigger -= 31
    await engine.evaluate()
    assert engine._monitor_and_fire.call_count == 2

    # And immediately again it's throttled once more.
    await engine.evaluate()
    assert engine._monitor_and_fire.call_count == 2
