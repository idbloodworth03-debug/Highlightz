"""
Clip titles must describe what was measured. The old titles named whichever
signal happened to edge out the rest ("Massive Pop-Off" for a viewer blip),
so titles routinely didn't match the clip. The dominance rule: a signal is
named only when it clearly led; multi-signal moments and broad low-grade
activity get honest generic titles.
"""

import asyncio

from src.trigger.engine import TriggerEngine
from src.trigger.signals import Signal, SignalType


def _engine():
    async def _noop(_):
        pass
    return TriggerEngine("nova", on_trigger=_noop)


def _sig(t, v):
    return Signal(type=t, value=v, channel="nova", metadata={})


def test_clear_leader_gets_its_own_label():
    eng = _engine()
    signals = [_sig(SignalType.CHAT_VELOCITY, 0.9), _sig(SignalType.AUDIO_SPIKE, 0.25),
               _sig(SignalType.KEYWORD, 0.1)]
    assert eng._generate_clip_title(signals) == "nova — Chat Erupts"

    signals = [_sig(SignalType.SILENCE_BURST, 1.0), _sig(SignalType.CHAT_VELOCITY, 0.28)]
    assert eng._generate_clip_title(signals) == "nova — Silence, Then Chaos"


def test_near_tie_between_strong_signals_is_not_attributed_to_one():
    # audio 0.86 vs velocity 0.84 — the old code would have called this "Big
    # Reaction" off a 0.02 edge. Now it's honestly multi-signal.
    eng = _engine()
    signals = [_sig(SignalType.AUDIO_SPIKE, 0.86), _sig(SignalType.CHAT_VELOCITY, 0.84),
               _sig(SignalType.SENTIMENT, 0.6)]
    assert eng._generate_clip_title(signals) == "nova — Everything Pops Off At Once"


def test_broad_weak_activity_gets_neutral_title():
    eng = _engine()
    signals = [_sig(SignalType.CHAT_VELOCITY, 0.4), _sig(SignalType.AUDIO_SPIKE, 0.35),
               _sig(SignalType.KEYWORD, 0.3)]
    assert eng._generate_clip_title(signals) == "nova — Hype Moment"


def test_nothing_measured_falls_back_to_plain_title():
    eng = _engine()
    assert eng._generate_clip_title([_sig(SignalType.CHAT_VELOCITY, 0.1)]) == "Clip from nova"
    assert eng._generate_clip_title([]) == "Clip from nova"
