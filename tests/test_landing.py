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
    assert ">10<" in html and ">25<" in html        # both tier prices shown
    # No self-serve trial exists — the page must not advertise free days.
    assert "free trial" not in html.lower() and "days free" not in html.lower()
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
    assert "How does billing work?" in html and "$10/month" in html and "$25/month" in html
    assert "roughly the last 30 seconds" in html   # no over-promising on length


def test_seo_layer():
    import json as _json, re as _re
    html = api.LANDING_HTML
    assert '<link rel="canonical" href="https://highlightz.app/">' in html
    # JSON-LD blocks parse and carry the right types
    blocks = _re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, _re.S)
    types = set()
    for b in blocks:
        data = _json.loads(b)          # must be valid JSON
        types.add(data.get("@type"))
    assert types == {"SoftwareApplication", "FAQPage"}
    faq = [_json.loads(b) for b in blocks if _json.loads(b)["@type"] == "FAQPage"][0]
    assert len(faq["mainEntity"]) == 10
    assert all("<" not in q["acceptedAnswer"]["text"] for q in faq["mainEntity"])  # plain text
    # Crawler surface
    assert "/robots.txt" in api._OPEN_PATHS and "/sitemap.xml" in api._OPEN_PATHS


def test_login_and_paywall_are_noindex():
    assert '<meta name="robots" content="noindex">' in api.LOGIN_HTML
    assert '<meta name="robots" content="noindex">' in api.PAYWALL_HTML


def test_delete_and_reject_are_separate_routes():
    # Deleting is housekeeping; rejecting is judgment. The Delete button used
    # to call /reject, so every cleanup taught the formula a false negative.
    # Lock the existence of the true-delete route so it can't regress.
    routes = {(r.path, m) for r in api.app.routes for m in (getattr(r, "methods", None) or [])}
    assert ("/clips/{clip_id}", "DELETE") in routes
    assert ("/clips/{clip_id}/reject", "POST") in routes


def test_showcase_cap_refuses_instead_of_silently_evicting(tmp_path, monkeypatch):
    # Adding past the cap used to drop the oldest entry silently — a clip
    # vanishing off the public page with no signal. It now 409s so the admin
    # screen can say "remove one first".
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    import pytest as _pytest
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json")
    full = [{"id": f"old{i}"} for i in range(api._SHOWCASE_MAX)]
    api._save_showcase(full)
    monkeypatch.setattr(api, "_clips", {"new": {"id": "new", "status": "approved",
                                                "platform": "twitch", "twitch_url": "https://t"}})
    monkeypatch.setattr(api, "_require_admin", lambda request: None)
    with patch.object(api, "broadcast", new=AsyncMock()):
        with _pytest.raises(api.HTTPException) as exc:
            asyncio.run(api.admin_toggle_showcase(MagicMock(), "new"))
    assert exc.value.status_code == 409
    # Nothing was evicted.
    assert [e["id"] for e in api._load_showcase()] == [f"old{i}" for i in range(api._SHOWCASE_MAX)]


def test_showcase_toggle_and_move_broadcast_and_reorder(tmp_path, monkeypatch):
    # Realtime contract: curation changes must reach open admin tabs, and the
    # landing page renders showcase order, so move must actually reorder.
    import asyncio
    from unittest.mock import AsyncMock, MagicMock, patch
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json")
    api._save_showcase([{"id": "a"}, {"id": "b"}, {"id": "c"}])
    monkeypatch.setattr(api, "_require_admin", lambda request: None)
    with patch.object(api, "broadcast", new=AsyncMock()) as bc:
        out = asyncio.run(api.admin_move_showcase(MagicMock(), "c", dir="up"))
        assert out["order"] == ["a", "c", "b"]
        assert bc.await_count == 1
        assert bc.await_args.args[0]["event"] == "showcase_updated"
        # Removing an existing entry also broadcasts.
        res = asyncio.run(api.admin_toggle_showcase(MagicMock(), "a"))
        assert res["featured"] is False and res["max"] == api._SHOWCASE_MAX
        assert bc.await_count == 2
    assert [e["id"] for e in api._load_showcase()] == ["c", "b"]
    # Moving at the edge is a no-op, not an error.
    with patch.object(api, "broadcast", new=AsyncMock()):
        assert asyncio.run(api.admin_move_showcase(MagicMock(), "c", dir="up"))["order"] == ["c", "b"]


def test_showcase_admin_routes_exist():
    routes = {(r.path, m) for r in api.app.routes for m in (getattr(r, "methods", None) or [])}
    assert ("/admin/showcase/{clip_id}", "POST") in routes
    assert ("/admin/showcase/{clip_id}/move", "POST") in routes
