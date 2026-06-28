"""
Tests for the append-only clip-outcome training log.

This is write-only telemetry that builds the labeled dataset (incl. the negative
class) a future weight-learning pass needs. It must never raise into the clip
pipeline and must record the signal vector + label correctly.
"""

import json
from pathlib import Path

from src.profiles import training_log as tl


def _clip(**kw):
    base = {
        "id": "c1", "channel": "jynxzi", "user_id": "u1", "platform": "twitch",
        "trigger_score": 72.0, "virality_score": 55.0,
        "trigger_signals": [
            {"type": "SignalType.CHAT_VELOCITY", "value": 0.8},
            {"type": "AUDIO_SPIKE", "value": 0.4},
        ],
    }
    base.update(kw)
    return base


def _read(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_logs_label_and_signal_vector(tmp_path, monkeypatch):
    f = tmp_path / "training_log.jsonl"
    monkeypatch.setattr(tl, "_LOG_FILE", f)
    tl.log_outcome(_clip(), tl.APPROVED)
    rows = _read(f)
    assert len(rows) == 1
    r = rows[0]
    assert r["label"] == "approved"
    assert r["channel"] == "jynxzi"
    # SignalType. prefix stripped, values preserved
    assert r["signals"] == {"CHAT_VELOCITY": 0.8, "AUDIO_SPIKE": 0.4}


def test_appends_multiple_outcomes_including_negatives(tmp_path, monkeypatch):
    f = tmp_path / "training_log.jsonl"
    monkeypatch.setattr(tl, "_LOG_FILE", f)
    tl.log_outcome(_clip(id="a"), tl.APPROVED)
    tl.log_outcome(_clip(id="b"), tl.REJECTED)
    tl.log_outcome(_clip(id="c"), tl.EXPIRED_UNREVIEWED)
    labels = [r["label"] for r in _read(f)]
    assert labels == ["approved", "rejected", "expired_unreviewed"]


def test_skips_clips_without_signals(tmp_path, monkeypatch):
    f = tmp_path / "training_log.jsonl"
    monkeypatch.setattr(tl, "_LOG_FILE", f)
    tl.log_outcome(_clip(trigger_signals=[]), tl.APPROVED)   # e.g. a VOD moment
    assert not f.exists() or _read(f) == []


def test_never_raises_on_bad_input(tmp_path, monkeypatch):
    f = tmp_path / "training_log.jsonl"
    monkeypatch.setattr(tl, "_LOG_FILE", f)
    # Malformed clip must not raise — telemetry can't break the clip pipeline.
    tl.log_outcome({"trigger_signals": [{"value": "not-a-number"}]}, tl.APPROVED)
    tl.log_outcome(None or {}, tl.REJECTED)
