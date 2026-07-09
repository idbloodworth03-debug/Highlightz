"""
Tests for the public landing-page stats counter.

The landing page shows an all-time "clips captured" ticker fed by
GET /landing/stats (unauthenticated — it's in _OPEN_PATHS). The counter is a
monotonic persisted total: seeded once from historical data (profile tallies +
current clip store), then incremented on every stored clip / VOD moment.
"""

import json

import pytest

from src.dashboard import api


@pytest.fixture()
def counter_env(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_CLIP_COUNTER_FILE", tmp_path / "clip_counter.json")
    monkeypatch.setattr(api, "_clip_counter", None)
    return tmp_path


def test_seed_uses_profiles_and_current_clips(counter_env, monkeypatch):
    profiles = counter_env / "profiles" / "u1"
    profiles.mkdir(parents=True)
    (profiles / "jynxzi.json").write_text(json.dumps({"total_clips": 40}))
    (profiles / "lacy.json").write_text(json.dumps({"total_clips": 12}))
    (profiles / "corrupt.json").write_text("{not json")   # must be skipped, not crash
    monkeypatch.setattr(api.settings, "local_storage_path", str(counter_env))
    monkeypatch.setattr(api, "_clips", {"a": {}, "b": {}, "c": {}})
    assert api.get_clip_counter() == 40 + 12 + 3
    # Seed is persisted so restarts don't re-derive (and can only grow from here).
    assert json.loads((counter_env / "clip_counter.json").read_text())["total"] == 55


def test_increment_persists_and_survives_reload(counter_env, monkeypatch):
    (counter_env / "clip_counter.json").write_text(json.dumps({"total": 100}))
    assert api.get_clip_counter() == 100
    api.increment_clip_counter()
    api.increment_clip_counter(2)
    assert api.get_clip_counter() == 103
    # Simulate process restart: cache cleared, file is the source of truth.
    monkeypatch.setattr(api, "_clip_counter", None)
    assert api.get_clip_counter() == 103


def test_landing_stats_route_is_public():
    # The endpoint must exist and be exempt from the auth gate, or the landing
    # page ticker 401s for logged-out visitors.
    assert "/landing/stats" in api._OPEN_PATHS
    assert any(getattr(r, "path", "") == "/landing/stats" for r in api.app.routes)


def test_landing_html_has_price_counter_and_demo():
    html = api.LANDING_HTML
    assert "$15" in html or ">15<" in html          # price shown
    assert "First 7 days free" in html               # trial shown
    assert 'id="lp-count"' in html                   # live counter element
    assert "/landing/stats" in html                  # fed by the public endpoint
    assert 'id="demo"' in html and "TRIGGER FIRED" in html   # live capture demo


def test_showcase_endpoint_public_and_curated(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json")
    assert "/landing/showcase" in api._OPEN_PATHS
    assert api._load_showcase() == []                       # empty by default
    clip = {"id": "c1", "clip_title": "T", "channel": "nova", "game": "VAL",
            "twitch_url": "https://t", "thumbnail_url": "https://i",
            "trigger_score": 91.4, "duration_seconds": 30, "user_id": "SECRET",
            "chat_snapshot": ["private"], "status": "approved", "platform": "twitch"}
    entry = api._showcase_entry(clip)
    # Whitelist only — nothing user-identifying or internal leaks to the public page.
    assert entry["score"] == 91 and entry["channel"] == "nova"
    assert "user_id" not in entry and "chat_snapshot" not in entry and "status" not in entry
    api._save_showcase([entry])
    assert api._load_showcase() == [entry]


def test_showcase_pruned_when_clip_dies(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json")
    api._save_showcase([{"id": "dead"}, {"id": "alive"}])
    api.prune_showcase({"dead"})
    assert [e["id"] for e in api._load_showcase()] == ["alive"]


def test_landing_has_examples_section():
    html = api.LANDING_HTML
    assert 'id="examples"' in html and 'id="ex-grid"' in html
    assert "/landing/showcase" in html
    assert 'id="nav-examples"' in html          # nav tab, revealed when data exists


def test_showcase_entry_carries_embed_url_for_inline_playback():
    entry = api._showcase_entry({"id": "c", "clip_title": "T", "channel": "n",
                                 "twitch_url": "https://t",
                                 "embed_url": "https://clips.twitch.tv/embed?clip=Slug",
                                 "trigger_score": 80})
    assert entry["embed_url"] == "https://clips.twitch.tv/embed?clip=Slug"


def test_landing_has_inline_clip_lightbox():
    html = api.LANDING_HTML
    # Visitors watch featured clips in-page (like the clip library), with a
    # Twitch link as the escape hatch; playback stops on close.
    assert 'id="exl"' in html and 'id="exl-iframe"' in html
    assert 'id="exl-out"' in html
    assert "about:blank" in html                 # close stops playback
    assert "parent='+location.hostname" in html  # Twitch embed parent param


def test_landing_has_faq_with_ten_items():
    html = api.LANDING_HTML
    assert 'id="faq"' in html
    assert html.count('class="faq-item"') == 10
    # A few key answers exist and stay honest
    assert "Is this AI?" in html and "transparent mathematical formula" in html
    assert "How does the free trial work?" in html and "$15/month" in html
    assert "roughly the last 30 seconds" in html   # no over-promising on length
