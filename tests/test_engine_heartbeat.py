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


# ── Add-stream suggestions ────────────────────────────────────────────────────

def test_stream_suggest_zero_state_recent_and_popular(tmp_path, monkeypatch):
    # Zero state: previously monitored channels (profile files, newest first)
    # + popular-now, with already-monitored channels filtered from both.
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.dashboard import api
    pdir = tmp_path / "profiles" / "u1"
    pdir.mkdir(parents=True)
    import os, time as _t
    for i, name in enumerate(["oldman", "jynxzi", "lacy"]):
        p = pdir / f"{name}.json"; p.write_text("{}")
        os.utime(p, (_t.time() - 100 + i, _t.time() - 100 + i))   # lacy newest
    monkeypatch.setattr(api.settings, "local_storage_path", str(tmp_path))
    monkeypatch.setattr(api, "_streams", {"s1": {"user_id": "u1", "channel": "jynxzi"}})
    monkeypatch.setattr(api, "_current_user_id", lambda request: "u1")
    monkeypatch.setattr(api, "_popular_streams_cache", (0.0, []))
    pop = [{"login": "kaicenat", "name": "KaiCenat", "game": "JC", "viewers": 90000},
           {"login": "jynxzi", "name": "Jynxzi", "game": "R6", "viewers": 50000}]
    with patch("src.output.twitch_clips.get_top_streams", new=AsyncMock(return_value=pop)):
        out = asyncio.run(api.stream_suggestions(MagicMock(), q=""))
    assert out["recent"] == ["lacy", "oldman"]                  # newest first, jynxzi filtered
    assert [p["login"] for p in out["popular"]] == ["kaicenat"]  # monitored filtered
    # Cache primed — second call must NOT hit Helix again inside the TTL.
    with patch("src.output.twitch_clips.get_top_streams",
               new=AsyncMock(side_effect=AssertionError("cache miss"))):
        out2 = asyncio.run(api.stream_suggestions(MagicMock(), q=""))
    assert [p["login"] for p in out2["popular"]] == ["kaicenat"]


def test_stream_suggest_search_filters_monitored(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.dashboard import api
    monkeypatch.setattr(api, "_streams", {"s1": {"user_id": "u1", "channel": "lacy"}})
    monkeypatch.setattr(api, "_current_user_id", lambda request: "u1")
    rows = [{"login": "lacy", "name": "Lacy", "avatar": "", "is_live": True, "game": "JC"},
            {"login": "lacari", "name": "Lacari", "avatar": "", "is_live": False, "game": ""}]
    with patch("src.output.twitch_clips.search_channels", new=AsyncMock(return_value=rows)):
        out = asyncio.run(api.stream_suggestions(MagicMock(), q="lac"))
    assert [r["login"] for r in out["results"]] == ["lacari"]
