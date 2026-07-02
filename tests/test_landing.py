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
