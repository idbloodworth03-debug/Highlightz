"""
Blind training studio: the labeler must never see the bot's judgment, and
every submission must be paired with the bot's hidden signals server-side.
"""

import json

from src.dashboard import api


def test_blind_view_strips_every_bot_judgment():
    clip = {
        "id": "c1", "channel": "nova", "game": "VALORANT", "created_at": 1.0,
        "duration_seconds": 30, "twitch_url": "https://t", "embed_url": "https://e",
        # Everything below must NOT reach the labeler:
        "trigger_score": 91.2, "virality_score": 60.0, "status": "approved",
        "clip_title": "nova — Chat Erupts",     # title names the dominant signal
        "trigger_signals": [{"type": "CHAT_VELOCITY", "value": 0.9}],
        "user_id": "u1", "chat_snapshot": ["secret"],
    }
    view = api._blind_clip_view(clip)
    assert view["id"] == "c1" and view["embed_url"] == "https://e"
    leaked = {"trigger_score", "virality_score", "status", "clip_title",
              "trigger_signals", "user_id", "chat_snapshot"} & set(view)
    assert not leaked, f"bot judgment leaked to labeler: {leaked}"


def test_score_record_pairs_human_with_hidden_bot_signals(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_HUMAN_SCORES_FILE", tmp_path / "human_scores.jsonl")
    clip = {
        "id": "c1", "channel": "nova", "trigger_score": 77.7, "virality_score": 41.0,
        "trigger_signals": [{"type": "SignalType.CHAT_VELOCITY", "value": 0.83},
                            {"type": "AUDIO_SPIKE", "value": 0.4}],
    }
    rec = api._record_human_score(clip, "u1", "alice",
                                  {"sentiment": 5, "audio": 7, "virality": 8})
    # Human sliders stored as ints, bot side joined without ever being shown.
    assert rec["human"] == {"sentiment": 5, "audio": 7, "virality": 8}
    assert rec["bot_virality_score"] == 41.0   # paired for the virality dimension
    assert rec["bot_signals"] == {"CHAT_VELOCITY": 0.83, "AUDIO_SPIKE": 0.4}
    assert rec["bot_trigger_score"] == 77.7
    # Persisted as one JSONL line, and the dedupe index sees it.
    lines = (tmp_path / "human_scores.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["labeler"] == "alice"
    assert ("c1", "u1") in api._human_scored_pairs()
    assert ("c1", "u2") not in api._human_scored_pairs()   # per-labeler dedupe


def test_labeler_flag_roundtrip(tmp_path, monkeypatch):
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    u = users.create("teammate", "hunter2hunter2")
    assert users.set_labeler(u["id"], True) is True
    assert users.get_by_id(u["id"])["is_labeler"] is True
    assert users.set_labeler(u["id"], False) is True
    assert users.get_by_id(u["id"])["is_labeler"] is False
    assert users.set_labeler("missing", True) is False


def test_spearman_sanity():
    import pytest
    from src.maintenance.analyze_human_scores import spearman
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)   # perfect
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)  # inverted
    assert spearman([1, 2], [2, 1]) is None                                 # too thin


def test_train_dimensions_exclude_unjudgeable_signals():
    # Chat velocity and keyword hits were removed from the sliders: a human
    # can't honestly rate message-rate spikes from watching a 30s clip, so
    # those scores were noise. Only watchable dimensions remain.
    assert set(api._TRAIN_DIMENSIONS) == {"sentiment", "audio"}
    fields = set(api._TrainScoreRequest.model_fields)
    assert fields == {"clip_id", "sentiment", "audio", "virality"}


def test_training_queue_is_oldest_first():
    # Trainers score chronologically from the first clip taken — the backlog
    # drains in capture order, newest clips don't jump the line.
    import inspect
    src = inspect.getsource(api.training_queue)
    assert "reverse=True" not in src
    assert 'sort(key=lambda c: c.get("created_at") or 0)' in src


def test_training_queue_excludes_already_reviewed_clips(monkeypatch, tmp_path):
    # A clip the labeler approved/rejected in Clip Review has been watched with
    # the bot's numbers visible — it can never be scored blind. Only unreviewed
    # clips belong in the queue.
    import asyncio
    from unittest.mock import MagicMock
    monkeypatch.setattr(api, "_HUMAN_SCORES_FILE", tmp_path / "human_scores.jsonl")
    monkeypatch.setattr(api, "_clips", {
        "a": {"id": "a", "user_id": "u1", "status": "pending",  "created_at": 3,
              "trigger_signals": [{"type": "SENTIMENT", "value": 0.5}]},
        "b": {"id": "b", "user_id": "u1", "status": "approved", "created_at": 1,
              "trigger_signals": [{"type": "SENTIMENT", "value": 0.5}]},
        "c": {"id": "c", "user_id": "u1", "status": "rejected", "created_at": 2,
              "trigger_signals": [{"type": "SENTIMENT", "value": 0.5}]},
        "d": {"id": "d", "user_id": "u1", "status": "pending",  "created_at": 1,
              "trigger_signals": [{"type": "SENTIMENT", "value": 0.5}]},
    })
    monkeypatch.setattr(api, "_require_labeler", lambda request: "u1")
    queue = asyncio.run(api.training_queue(MagicMock()))
    # Reviewed clips (b approved, c rejected) are gone; the rest oldest-first.
    assert [c["id"] for c in queue] == ["d", "a"]
