"""The decoding knobs reach Whisper, and default to what was already running."""
from pathlib import Path
import pytest
from config.settings import Settings
from src.captions import transcribe as cap


class FakeModel:
    def __init__(self): self.calls = []
    def transcribe(self, wav, **kw):
        self.calls.append(kw)
        class I: language = "en"
        return [], I()


def test_the_defaults_are_the_measured_good_trade():
    """This test used to pin tiny.en/greedy/no-prompt, because adding a knob
    must not move a setting. The setting then moved DELIBERATELY (2026-09-15):
    the A/B on prod showed tiny.en mis-hearing and greedy decoding truncating
    the tail of what was said, and base.en with a beam of 5 reading correctly.
    What this pins now is that nobody quietly slides back to the cheap
    settings, and that the prompt stays generic — it names a register, never a
    word a small model could be tempted to insert."""
    s = Settings()
    assert s.captions_model == "base.en"
    assert s.captions_beam_size == 5
    assert s.captions_initial_prompt
    for specific in ("kaicenat", "ishowspeed", "fortnite", "warzone", "gg"):
        assert specific not in s.captions_initial_prompt.lower(), \
            "the prompt names a specific word the model may start inserting"


def test_beam_and_prompt_are_passed_through(monkeypatch):
    m = FakeModel()
    cap._run_whisper(Path("x.wav"), model=m, beam=5, prompt="gameplay")
    assert m.calls[0]["beam_size"] == 5
    assert m.calls[0]["initial_prompt"] == "gameplay"


def test_an_empty_prompt_is_sent_as_none_not_an_empty_string(monkeypatch):
    """faster-whisper treats "" and None differently; an empty string is still
    a prompt and shifts the decode."""
    m = FakeModel()
    cap._run_whisper(Path("x.wav"), model=m, prompt="")
    assert m.calls[0]["initial_prompt"] is None


def test_beam_can_never_be_zero_or_negative(monkeypatch):
    """It arrives from .env, where a typo is a string away from crashing the
    decoder inside a background job nobody is watching."""
    m = FakeModel()
    cap._run_whisper(Path("x.wav"), model=m, beam=0)
    # Zero is "not given", so it takes the configured default (5 now, was 1);
    # what matters is that the decoder never sees zero or less.
    assert m.calls[0]["beam_size"] == Settings().captions_beam_size >= 1
    cap._run_whisper(Path("x.wav"), model=m, beam=-3)
    assert m.calls[1]["beam_size"] >= 1


def test_the_service_path_still_uses_the_one_cached_model(monkeypatch):
    """The tool loads several sizes in one process; the service must not start
    doing that — a second model is a few hundred MB on a 2 GB box."""
    m = FakeModel()
    monkeypatch.setattr(cap, "_model", m)
    cap._run_whisper(Path("x.wav"))
    assert m.calls, "the cached model was bypassed"


def test_settings_are_read_when_nothing_is_passed(monkeypatch):
    m = FakeModel()
    monkeypatch.setattr(cap.settings, "captions_beam_size", 5)
    monkeypatch.setattr(cap.settings, "captions_initial_prompt", "twitch")
    cap._run_whisper(Path("x.wav"), model=m)
    assert m.calls[0]["beam_size"] == 5
    assert m.calls[0]["initial_prompt"] == "twitch"
