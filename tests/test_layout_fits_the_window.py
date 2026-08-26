"""The app must fit the window, whoever is looking at it.

REPORTED AS: "the site goes off the screen on the bottom on everything."

TWO CAUSES, BOTH MEASURED IN A BROWSER BEFORE ANYTHING WAS CHANGED.

1. A ROW TEMPLATE THAT COULD NOT COUNT ITS CHILDREN. `.rd-frame` was
   `grid-template-rows: 68px 1fr` with THREE children — header, trial banner,
   screen. A two-row template gives row 2 (the BANNER) the 1fr and drops the
   screen into an implicit auto row, so what happened depended entirely on how
   tall the screen's content was:

       Clip Review   resolved 68 /  44 / 863   fine, by luck
       Settings      resolved 68 / 381 / 526   a 44px banner became 381px
       Feedback      resolved 68 / 436 / 470   and 436px

   Hundreds of pixels of empty purple under the header on any screen whose
   content did not fill the window — visible in a screenshot taken during an
   earlier task and dismissed as a capture artifact, which it was not.

2. AN IMPLICIT AUTO ROW ON THE APP. `.rd-app` is `height:100vh` but declared no
   `grid-template-rows`, so its single row was implicit and `auto` — sized to
   the frame's max-content. A tall screen therefore made the frame TALLER than
   the app (measured: 1000px of frame in a 975px window, 790 in 700), and
   `.rd-frame{overflow:hidden}` clipped the difference. No scrollbar, no
   scroll, just gone.

A third, unrelated one turned up in the same sweep and is fixed here too: in
the 701-900px width band — between the desktop layout and the page-scroll
mobile mode, which nothing had ever been checked at — the stacked streams
layout had no scroller of its own, leaving its last control 7802px below a
900px window. Verified against the pre-change code with `git stash`: identical
there, so it predates this work.

WHY THESE TESTS LOOK LIKE THIS. What actually proved the bug and the fix was
measuring rendered geometry in Chromium across nine screens, two account types
and three viewport heights; pytest cannot do that. So these hold down the
STRUCTURAL invariant that made the bug possible — a height distribution that
depends on how many children happen to be present — rather than re-asserting
pixel numbers a test here cannot see.
"""

import re
from pathlib import Path

import pytest

SRC = Path("src/dashboard/aurora_html.py").read_text()
CSS = SRC.split('<script type="text/babel">')[0]
JS = SRC.split('<script type="text/babel">')[1]


def _rule(selector: str) -> str:
    """The declarations of the base rule for EXACTLY this selector.

    Anchored at a line start, because a bare `.rd-screen` pattern also matches
    inside `.rd-frame > .rd-screen{...}` — which is a different rule, and
    reading it instead reports the wrong declarations with total confidence.
    """
    m = re.search(r"(?:^|\n)" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"{selector} rule not found"
    return m.group(1)


# ── the invariant that makes the bug impossible ──────────────────────────────

def test_the_frame_does_not_divide_its_height_by_a_fixed_row_count():
    """THE ROOT CAUSE. A row template has to know how many children there are.
    This frame has two or three depending on whether the user is on a trial —
    a fact the stylesheet cannot see — so any fixed template is wrong for one
    of those cases. A flex column cannot get it wrong."""
    assert "grid-template-rows" not in CSS.split(".rd-frame{")[1].split("}")[0], \
        "the frame is back to a fixed row template"
    for m in re.finditer(r"\.rd-frame[^{,]*\{([^}]*)\}", CSS):
        assert "grid-template-rows" not in m.group(1), (
            "a media query gives .rd-frame a row template again — the same "
            "mistake, just at another width")


def test_the_frame_is_a_column_whose_screen_takes_what_is_left():
    rule = _rule(".rd-frame")
    assert "display:flex" in rule and "flex-direction:column" in rule
    assert re.search(r"\.rd-frame > \*\{flex:0 0 auto\}", CSS), \
        "children can shrink or stretch — the banner will resize again"
    assert re.search(r"\.rd-frame > \.rd-screen\{flex:1 1 auto\}", CSS), \
        "nothing absorbs the leftover height"


def test_the_header_carries_its_own_height_now():
    """It used to get 68px from the row template. Without the template it
    collapses to its content and the whole header goes thin."""
    assert re.search(r"\.rd-header\{height:68px\}", CSS), \
        "the header lost the height the row template used to give it"


def test_the_app_pins_its_row_instead_of_letting_it_grow():
    """An implicit `auto` row sizes to max-content, so a tall screen pushed the
    frame past 100vh and overflow:hidden ate the difference."""
    rule = _rule(".rd-app")
    assert "height:100vh" in rule
    assert "grid-template-rows:minmax(0,1fr)" in rule, (
        "the app's row is implicit and auto again — the frame can grow past "
        "the window and the bottom becomes unreachable")


def test_the_frame_still_clips_rather_than_growing():
    """overflow:hidden is what makes the row pinning load-bearing: together
    they mean 'fit exactly', and the inner scrollers do the scrolling."""
    assert "overflow:hidden" in _rule(".rd-frame")


# ── the mobile mode, which deliberately does the opposite ────────────────────

def _media_block(query: str) -> str:
    i = CSS.index(query)
    depth, out = 0, []
    for ch in CSS[i:]:
        out.append(ch)
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
    return "".join(out)


def test_the_narrow_layout_goes_back_to_scrolling_the_page():
    """Below 700px the app is meant to be one long scrolling page, not a
    pinned frame. Pinning the row at every width would trap it in one screen —
    the opposite bug, and the fix for the desktop one causes it if the
    breakpoint is not given its own value back."""
    block = _media_block("@media(max-width:700px)")
    assert "height:auto" in block and "min-height:100dvh" in block
    assert "grid-template-rows:auto" in block, \
        "the narrow layout inherits the pinned row and cannot scroll"
    assert re.search(r"\.rd-frame > \.rd-screen\{flex:0 0 auto\}", block), \
        "the screen still tries to fill a frame that no longer has a height"


def test_the_stacked_streams_layout_can_scroll():
    """Between 701 and 900px the two columns stack, which makes the layout
    taller than the window while nothing scrolls it. Measured at 768x900: the
    last control sat 7802px down. Present in the pre-change code too."""
    block = _media_block("@media(max-width:900px)")
    assert re.search(r"\.rd-streams-layout\{[^}]*overflow-y:auto", block), \
        "the stacked streams layout has no scroller, so its bottom is lost"


# ── what the JSX is allowed to assume ────────────────────────────────────────

def test_the_banner_really_is_a_third_child_of_the_frame():
    """If it were moved inside .rd-screen the row-count problem would be gone
    and these tests would be guarding nothing. It is a sibling, conditionally
    rendered, which is exactly why a fixed row template cannot work."""
    m = re.search(r'<div className="rd-frame">(.*?)\n      </div>', JS, re.S)
    assert m, "the frame markup moved"
    body = m.group(0)
    assert "<header className=\"rd-header\">" in body
    assert "subscription_status==='trialing' &&" in body, \
        "the trial banner is no longer a conditional sibling"
    assert '<main className="rd-screen">' in body
    assert body.index("subscription_status==='trialing'") < body.index('className="rd-screen"'), \
        "the banner must sit between the header and the screen"


@pytest.mark.parametrize("selector", [".rd-screen", ".rd-scroll"])
def test_the_inner_boxes_can_still_shrink(selector):
    """min-height:0 is what lets a flex child be smaller than its content so
    its own scrollbar appears. Without it the child pushes its parent taller
    and the overflow moves up the tree until something clips it."""
    assert "min-height:0" in _rule(selector), \
        f"{selector} lost min-height:0 and will push its parent open"
