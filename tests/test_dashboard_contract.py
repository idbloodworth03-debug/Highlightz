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


def _kick_blocked() -> set[str]:
    m = re.search(r"const KICK_BLOCKED=\[(.*?)\];", SRC, re.S)
    assert m, "KICK_BLOCKED table not found"
    return set(re.findall(r"'(\w+)'", m.group(1)))


def test_kick_blocked_tabs_are_real_nav_tabs():
    """A typo'd id here would silently block nothing."""
    unknown = _kick_blocked() - _nav_routes()
    assert not unknown, f"KICK_BLOCKED lists ids that are not NAV tabs: {sorted(unknown)}"


def test_kick_blocked_nav_buttons_are_actually_disabled_not_just_dimmed():
    """Greying a button out without disabling it leaves it clickable — the user
    lands on a dead tab and the app looks broken rather than closed.

    The click handler must also bail, because `disabled` alone does not stop a
    programmatic or keyboard-triggered click in every browser.
    """
    nav = re.search(r"NAV\.filter\(.*?\}\)\}", SRC, re.S)
    assert nav, "nav render block not found"
    block = nav.group(0)
    assert "const blocked = activePlatform==='kick' && KICK_BLOCKED.includes(n.id)" in block
    # The lookbehind matters: `aria-disabled={blocked}` contains the literal
    # `disabled={blocked}`, so a plain substring check passes even when the
    # real `disabled` attribute has been removed and the button is clickable
    # again. Only the standalone attribute actually blocks the click.
    assert re.search(r"(?<![-\w])disabled=\{blocked\}", block), \
        "blocked tab is styled/aria-marked but still clickable"
    assert "if(blocked) return;" in block, "click handler does not bail on a blocked tab"


def test_kick_never_traps_the_user():
    """Everything platform-specific is closed on Kick, so the ways OUT must
    stay open or the only escape is a page refresh."""
    blocked = _kick_blocked()
    for escape in ("account", "feedback"):
        assert escape not in blocked, f"{escape} must stay reachable from Kick"
    # The platform switch and sign-out live outside the NAV loop entirely, so
    # KICK_BLOCKED cannot reach them; assert they are still rendered.
    assert "switchPlatform('twitch')" in SRC
    assert "/logout" in SRC


def test_held_back_features_default_to_closed_while_me_is_loading():
    """`me` is null on first paint. If the flag defaulted open, the real screen
    would flash before /me arrives and then get yanked away."""
    m = re.search(r"const uploadsOn = ([^;]+);", SRC)
    assert m, "uploadsOn not found"
    expr = m.group(1)
    assert "me &&" in expr, "uploadsOn must be false until /me has loaded"
    assert "features?.uploads" in expr and "is_admin" in expr


def test_a_failed_clip_import_leaves_the_retry_button_in_place():
    """`started` gates the whole import UI: false shows the load button, true
    shows the results view.

    It was originally set in a `finally`, so a FAILED first load flipped it
    true — the retry button vanished and the user was left with an error
    message sitting next to "No clips on your channel yet". Two contradictory
    statements and no way forward. It must only be set on the success path.
    """
    m = re.search(r"const fetchPage = useCallback\(async \(cur\) => \{(.*?)\n  \}, \[\]\);",
                  SRC, re.S)
    assert m, "fetchPage not found"
    body = m.group(1)
    fin = re.search(r"finally \{([^}]*)\}", body)
    assert fin, "no finally block in fetchPage"
    assert "setStart" not in fin.group(1), \
        "setStart in finally — a failed load hides the retry button"
    assert "setStart(true)" in body, "success path never marks the load complete"
