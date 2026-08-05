"""The clip counter has to be in the HTML, not fetched by JavaScript.

Crawlers, link unfurlers and AI readers overwhelmingly parse the raw response
and never execute JS. The counter used to ship as a `display:none` tile
containing a literal `0`, so those readers did not merely miss the number —
they saw zero, which invites "Highlightz has captured 0 clips".
"""

import json
import re

import pytest

from src.dashboard import api


@pytest.fixture
def counted(monkeypatch):
    def _set(n):
        monkeypatch.setattr(api, "get_clip_counter", lambda: n)
        return api.render_landing()
    return _set


def _visible_number(html: str) -> str:
    m = re.search(r'id="lp-count">([^<]*)<', html)
    assert m, "the counter element is gone"
    return m.group(1)


def _jsonld(html: str) -> dict:
    blob = html[html.index('{"@context"'):]
    return json.loads(blob[:blob.index("</script>")])


def test_the_number_is_in_the_html_without_running_any_javascript(counted):
    html = counted(12345)
    assert "12,345" in html
    assert _visible_number(html) == "12,345"


def test_the_tile_is_not_hidden_once_there_is_a_number(counted):
    """A hidden element is one an extractor may skip even if it parses the
    text, and it is invisible to a human reading a rendered snapshot."""
    html = counted(500)
    assert 'id="stat-clips" style="display:none"' not in html
    assert 'id="stat-clips"' in html


def test_no_literal_zero_is_left_in_the_counter(counted):
    """The specific failure being fixed: readers saw 0, not 'nothing'."""
    assert _visible_number(counted(7)) != "0"


def test_the_count_is_also_in_the_structured_data(counted):
    """schema.org interactionStatistic is where a machine looks for a count —
    more reliable than scraping a styled <span>."""
    d = _jsonld(counted(98765))
    stat = d["interactionStatistic"]
    assert stat["@type"] == "InteractionCounter"
    assert stat["userInteractionCount"] == 98765


def test_the_structured_data_stays_valid_json(counted):
    """It is inside a <script type=application/ld+json>. One bad substitution
    and the whole block is silently discarded by every consumer."""
    _jsonld(counted(1))
    _jsonld(counted(0))
    _jsonld(counted(1234567))


def test_zero_clips_shows_nothing_rather_than_claiming_zero(counted):
    """Before any clips exist, hiding the tile is right — advertising a real
    zero is worse than saying nothing."""
    html = counted(0)
    assert 'id="stat-clips" style="display:none"' in html
    assert _jsonld(html)["interactionStatistic"]["userInteractionCount"] == 0


def test_the_number_is_formatted_for_humans_but_raw_for_machines(counted):
    html = counted(1234567)
    assert _visible_number(html) == "1,234,567"
    # JSON-LD must be a NUMBER, not "1,234,567" — a comma-formatted string is
    # not a valid schema.org count and consumers will drop it.
    assert _jsonld(html)["interactionStatistic"]["userInteractionCount"] == 1234567


def test_the_landing_route_serves_the_rendered_html_not_the_constant():
    """Easy to add render_landing() and forget to actually use it."""
    import inspect
    src = inspect.getsource(api.dashboard)
    assert "render_landing()" in src
    assert "content=LANDING_HTML" not in src


def test_the_animation_starts_from_the_rendered_value():
    """Counting up from 0 would wipe the server-rendered number for a second
    and put a literal 0 back in the DOM — the exact state a crawler might
    sample."""
    assert "var from=parseInt(" in api.LANDING_HTML
    assert "Math.round(from+(target-from)*eased)" in api.LANDING_HTML
    assert "Math.round(target*eased)" not in api.LANDING_HTML, \
        "the animation still ramps from zero"
