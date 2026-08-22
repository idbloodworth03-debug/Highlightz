"""Where a featured clip appears: the hero wall, the examples grid, or both.

ONE CURATED LIST, TWO DESTINATIONS, and they want different things. The hero
wall draws four tiles of channels being scored, so it wants variety ACROSS
channels — four clips from one streamer makes the section argue against the
product, which is that it watches several at once. The examples grid is a
spread of the best clips and can repeat a channel happily.

Before this they were the same list, so there was no way to tune one without
wrecking the other.

THE BACKWARDS-COMPATIBILITY CASE IS THE DANGEROUS ONE. Entries curated before
placement existed carry neither key. Defaulting them to "neither" would blank
the live landing page the moment this deployed, so they default to BOTH — which
is exactly what they were already doing.
"""

import json

import pytest

from src.dashboard import api
from src.dashboard.aurora_html import DASHBOARD_HTML


def _dash() -> str:
    return DASHBOARD_HTML


@pytest.fixture(autouse=True)
def showcase(tmp_path, monkeypatch):
    monkeypatch.setattr(api, "_SHOWCASE_FILE", tmp_path / "showcase.json")
    yield tmp_path / "showcase.json"


def _write(entries, path):
    path.write_text(json.dumps(entries))


# ── the default, and the upgrade path ────────────────────────────────────────

def test_a_new_entry_appears_in_both_places():
    e = api._showcase_entry({"id": "c1", "channel": "nova", "clip_title": "x"})
    assert e["hero"] is True and e["gallery"] is True


def test_entries_curated_before_placement_existed_still_show(showcase):
    """The deploy case. These have neither key; treating that as "nowhere"
    would empty the live landing page on restart."""
    _write([{"id": "c1", "channel": "nova", "clip_title": "old entry"}], showcase)
    got = api._load_showcase()
    assert got[0]["hero"] is True, "an existing featured clip vanished from the hero"
    assert got[0]["gallery"] is True, "an existing featured clip vanished from the examples"


def test_an_explicit_false_is_not_overwritten_by_the_default(showcase):
    """setdefault, not assignment. Getting this backwards would make the
    toggles look like they work and then silently undo themselves on reload."""
    _write([{"id": "c1", "channel": "nova", "hero": False, "gallery": True}], showcase)
    got = api._load_showcase()
    assert got[0]["hero"] is False
    assert got[0]["gallery"] is True


# ── the two consumers actually filter ────────────────────────────────────────

def _landing_js():
    return api.LANDING_HTML


def test_the_hero_wall_takes_only_hero_clips():
    assert "c.hero !== false" in _landing_js(), \
        "the wall no longer filters by placement; it shows the gallery's clips too"


def test_the_examples_grid_takes_only_gallery_clips():
    assert "c.gallery !== false" in _landing_js(), \
        "the grid no longer filters by placement"


def test_both_filters_use_not_false_rather_than_truthy():
    """`c.hero` alone would drop every entry saved before placement existed,
    because the key is absent — the same blank-the-page failure as above, but
    on the client."""
    js = _landing_js()
    assert "c.hero !== false" in js and "c.gallery !== false" in js
    assert "if(c.hero)" not in js and "if(c.gallery)" not in js


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_placement_endpoint_rejects_an_unknown_destination():
    import inspect
    src = inspect.getsource(api.admin_showcase_placement)
    assert '("hero", "gallery")' in src, \
        "any string can be written onto a featured entry"
    assert "status_code=400" in src


def test_the_placement_endpoint_is_admin_only():
    import inspect
    src = inspect.getsource(api.admin_showcase_placement)
    assert "_require_admin(request)" in src, \
        "anyone signed in can rewrite the public landing page"


def test_changing_placement_broadcasts_so_open_tabs_update():
    """The realtime rule: a mutation a user can see has to reach the tab over
    the socket. showcase_updated already has a handler that refetches."""
    import inspect
    src = inspect.getsource(api.admin_showcase_placement)
    assert 'broadcast({"event": "showcase_updated"})' in src
    assert "msg.event==='showcase_updated'" in _dash(), \
        "nothing on the client listens for the event the server sends"


# ── the admin screen ─────────────────────────────────────────────────────────

def test_the_admin_screen_offers_both_toggles():
    html = _dash()
    assert "onPlace" in html, "the placement action is never passed to the screen"
    assert "'hero'" in html and "'gallery'" in html
    assert "/placement" in html, "the screen never calls the endpoint"


def test_the_admin_screen_warns_when_the_wall_would_repeat_itself():
    """Four tiles and fewer than four hero clips means the wall shows the same
    channel twice, which argues against the thing the section exists to show.

    Asserts the RENDERED WARNING, not the variable name. Checking for
    "heroShort" passed against a rename to "heroShortGone" — a substring match
    that let a dead warning through."""
    html = _dash()
    assert "{heroShort && " in html, "the under-filled warning is not rendered"
    assert "so it will repeat" in html, "the warning says nothing useful"
    assert "heroChannels.size < 4" in html, \
        "nothing notices that the hero clips are all from one channel"
    assert "several channels being watched at once" in html, \
        "the one-channel warning does not explain why it matters"
