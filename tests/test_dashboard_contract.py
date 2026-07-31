"""
Dashboard internal contracts — the ones a JSX syntax check cannot catch.

The app is React via Babel-standalone with no bundler and no type checker, so
a route that is wired in one table but missing from another compiles perfectly
and then throws at runtime. Because the throw happens inside the top-level
RdApp render, React unmounts the whole tree: the user gets a white screen, not
a broken tab.

That is exactly what happened when Clip Upload was added to NAV but not to
HEAD — `HEAD[route][0]` on an undefined entry took down the entire dashboard.
These tests keep every per-route table in step.
"""

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent.parent / "src/dashboard/aurora_html.py"
SRC = FRONTEND.read_text()


def _nav_routes() -> set[str]:
    nav = re.search(r"const NAV=\[(.*?)\];", SRC, re.S)
    assert nav, "NAV table not found"
    return set(re.findall(r"\{id:'(\w+)'", nav.group(1)))


def _head_routes() -> set[str]:
    head = re.search(r"const HEAD=\{(.*?)\};", SRC, re.S)
    assert head, "HEAD table not found"
    return set(re.findall(r"(\w+):\[", head.group(1)))


def _dispatch_routes() -> set[str]:
    return set(re.findall(r"route==='(\w+)'", SRC))


def test_every_nav_tab_has_a_header_entry():
    """A NAV id with no HEAD entry white-screens the app the moment it is
    clicked, because RdApp reads HEAD[route][0] unguarded."""
    missing = _nav_routes() - _head_routes()
    assert not missing, (
        f"NAV tabs with no HEAD entry — clicking these white-screens the whole "
        f"dashboard: {sorted(missing)}"
    )


def test_every_nav_tab_renders_a_screen():
    """A NAV id with no branch in the route dispatch silently falls through to
    the final `else` (Settings), so the tab appears to do nothing."""
    # 'settings' is the dispatch's terminal else-branch, so it never appears as
    # an explicit route=== comparison.
    missing = _nav_routes() - _dispatch_routes() - {"settings"}
    assert not missing, f"NAV tabs with no screen in the route dispatch: {sorted(missing)}"


def test_no_header_entries_for_routes_that_do_not_exist():
    """The reverse drift: a HEAD entry left behind after a tab is removed is
    dead weight that misleads the next person editing these tables."""
    stale = _head_routes() - _nav_routes()
    assert not stale, f"HEAD entries with no NAV tab: {sorted(stale)}"


@pytest.mark.parametrize("route", sorted(_nav_routes()))
def test_route_is_wired_into_every_table(route):
    """Per-route so a failure names the offending tab directly."""
    assert route in _head_routes(), f"{route!r} missing from HEAD"
    assert route in _dispatch_routes() or route == "settings", \
        f"{route!r} missing from the route dispatch"
