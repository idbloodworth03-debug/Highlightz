"""Clips must play smoothly WINDOWED, not only in fullscreen.

REPORTED TWICE. First on 2026-08-14, fixed for the review modal; again on
2026-08-25, because the first fix was aimed at the wrong thing.

THE SYMPTOM IS THE DIAGNOSIS. A fullscreen element renders in the browser's top
layer and the page underneath stops being painted at all. So anything that is
smooth fullscreen and juddery windowed is being paid for by work the PAGE is
doing — not by the video, the network or the embed.

WHAT THE FIRST FIX GOT WRONG. It read the symptom as "something is painted ON
the player" and removed the backdrop-filter from the four elements that overlay
it. That helped and was not the mechanism. The page behind a player keeps every
other backdrop-filter alive, frame after frame, and on a review queue that is
the nav, the header, four stat tiles and one blurred ring per card — 47 live
blur layers with 40 clips loaded. Measured on the real page with a canvas
repainting every frame inside the real modal:

    nothing changed   45.6 fps   p95 33.4ms   29% of frames dropped  (3 clips)
    nothing changed   42.8 fps   p95 33.4ms   36% of frames dropped  (40 clips)
    nav blur off      58.3 fps   p95 16.8ms    2%
    header blur off   59.1 fps   p95 16.8ms    1%
    stat tiles off    59.6 fps   p95 16.8ms    0%
    as shipped        59.5 fps   p95 16.7ms    0%

Removing ANY ONE of them recovers most of the budget, which is why hunting for
a single culprit was the wrong shape of investigation: the page simply cannot
keep several blur layers alive next to a decoding video.

THE FIX. While a player is on screen the page keeps NO blur layers at all,
via body.hz-player. The scrims over them are 86-88% opaque, so none of that
blur is visible while a player is up: a pixel diff of the modal with and
without the change found zero pixels differing by more than 8/255.

THE TEST THAT LET IT COME BACK is the reason this file asserts a SET rather
than a list. The old one took the four selectors that had been fixed and froze
them, so the five nobody had thought of were never checked.
"""

import re
from pathlib import Path

import pytest

SRC = Path("src/dashboard/aurora_html.py").read_text()
CSS = SRC.split('<script type="text/babel">')[0]
JS = SRC.split('<script type="text/babel">')[1]


def _blurring_selectors() -> set[str]:
    """Every selector in the stylesheet that turns a backdrop-filter ON."""
    found = set()
    for m in re.finditer(r"([^{}/]+)\{([^{}]*)\}", CSS):
        if re.search(r"backdrop-filter\s*:\s*(?!none)", m.group(2)):
            found.add(" ".join(m.group(1).split()))
    return found


def _switched_off_while_playing() -> set[str]:
    """Selectors the body.hz-player rule turns back off."""
    m = re.search(r"((?:body\.hz-player [^{},]+,\s*(?:/\*.*?\*/\s*)?)*"
                  r"body\.hz-player [^{},]+)\{[^{}]*backdrop-filter:\s*none[^{}]*\}",
                  CSS, re.S)
    assert m, "the body.hz-player rule is gone — nothing switches the blur off"
    out = set()
    for part in re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S).split(","):
        part = " ".join(part.split())
        if part.startswith("body.hz-player "):
            out.add(part[len("body.hz-player "):])
    return out


# ── the rule that generalises ────────────────────────────────────────────────

def test_every_blur_in_the_stylesheet_is_switched_off_while_a_clip_plays():
    """THE TEST THE LAST FIX NEEDED AND DID NOT HAVE.

    Not a hand-picked list — the set of selectors that turn a blur on must
    equal the set the player rule turns off. Add a backdrop-filter anywhere and
    this fails, and the person adding it has to decide whether it may stay
    alive next to a decoding video. The answer is almost certainly no."""
    on, off = _blurring_selectors(), _switched_off_while_playing()
    missing = sorted(on - off)
    assert not missing, (
        "these carry a backdrop-filter that stays live while a clip is "
        "playing: " + str(missing) + ". Add them to the body.hz-player rule — "
        "a page that keeps blur layers alive next to a decoding video drops "
        "frames windowed and is smooth only in fullscreen.")


def test_the_player_rule_does_not_switch_off_blurs_that_do_not_exist():
    """A stale entry is a lie about what the rule covers, and it is how the
    list drifts back out of step with the stylesheet."""
    on, off = _blurring_selectors(), _switched_off_while_playing()
    stale = sorted(off - on)
    assert not stale, f"body.hz-player names selectors that never blur: {stale}"


def test_the_stylesheet_has_no_text_stranded_outside_a_rule():
    """A COMMENT THAT CLOSES EARLY SILENTLY KILLS THE RULE AFTER IT, and it
    happened while writing this very fix: an edit left a paragraph of prose
    sitting between `*/` and the selector list, the browser swallowed the whole
    thing as one malformed rule, and the player kept all 47 blur layers. Every
    Python test still passed, because they read the selector list out of the
    file and never asked whether a browser would.

    So: strip the comments, then require that what is left is nothing but
    rules, at-rules and braces. Stray prose means a comment is unbalanced."""
    body = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    body = body[body.index("<style>") + len("<style>"):body.index("</style>")]
    stranded = []
    depth = 0
    buf = ""
    for ch in body:
        if ch == "{":
            depth += 1
            buf = ""
        elif ch == "}":
            depth -= 1
            # Between rules only whitespace and the next selector may appear;
            # a selector is always followed by '{', so anything left when the
            # next '}' arrives at depth 0 is text nobody will ever apply.
            buf = ""
        elif depth == 0:
            buf += ch
            if len(buf.strip()) > 400:
                stranded.append(buf.strip()[:120])
                buf = ""
    assert depth == 0, "unbalanced braces in the dashboard stylesheet"
    assert not stranded, (
        "text stranded outside any rule — a comment almost certainly closes "
        "early, which kills the rule that follows it:\n  " + "\n  ".join(stranded))
    assert CSS.count("/*") == CSS.count("*/"), \
        "unbalanced CSS comment markers in the dashboard stylesheet"


def test_the_set_is_not_empty_in_either_direction():
    """Guards the parsers themselves: two empty sets are equal, and would make
    both tests above pass while checking nothing at all."""
    assert len(_blurring_selectors()) >= 5
    assert len(_switched_off_while_playing()) >= 5


@pytest.mark.parametrize("selector", [".glass", ".rd-header", ".rd-nav", ".ed-bg"])
def test_the_ones_that_actually_wrap_or_back_a_player_are_named(selector):
    """Belt and braces on the parser: if the regex above ever silently stops
    matching, these four still have to be there by name."""
    assert selector in _switched_off_while_playing(), \
        f"{selector} is no longer switched off while a clip plays"


# ── the wiring that turns the class on and off ───────────────────────────────

def test_the_class_is_reference_counted_not_a_boolean():
    """Two players can be up at once — the review modal over the library, the
    import lightbox over the editor. With a plain add/remove the first one to
    close strips the class while the other is still playing, so the bug returns
    for exactly the case where the machine is busiest."""
    m = re.search(r"function usePlayerOpen\(open\) \{(.*?)\n\}", JS, re.S)
    assert m, "usePlayerOpen not found"
    body = m.group(1)
    assert "_hzPlayers += 1" in body, "nothing counts players in"
    assert re.search(r"_hzPlayers\s*=\s*Math\.max\(0, _hzPlayers - 1\)", body), \
        "nothing counts players out"
    assert re.search(r"if\(_hzPlayers === 0\) document\.body\.classList\.remove", body), \
        "the class is removed without checking whether another player is open"


def test_the_hook_cleans_up():
    """A class left on after the player closes takes the glass away from
    ordinary browsing, which is a visible regression rather than a slow one."""
    m = re.search(r"function usePlayerOpen\(open\) \{(.*?)\n\}", JS, re.S)
    assert "return ()=>{" in m.group(1), "the effect has no cleanup"


@pytest.mark.parametrize("component,arg", [
    ("ClipModal", "usePlayerOpen(!!clip)"),
    ("TwitchImport", "usePlayerOpen(!!play)"),
    ("ClipEditor", "usePlayerOpen(true)"),
])
def test_every_component_that_shows_a_player_marks_the_page(component, arg):
    m = re.search(r"function " + component + r"\(.*?\n\}\n\n", JS, re.S)
    assert m, f"{component} not found"
    assert arg in m.group(0), \
        f"{component} shows a player but never marks the page as playing"


def test_the_hook_runs_before_the_modal_bails_out_on_a_null_clip():
    """ClipModal is always mounted and returns null when there is no clip, so a
    hook after that return runs on some renders and not others — React throws
    on the render where the count changes."""
    m = re.search(r"function ClipModal\(.*?\n\}\n\n", JS, re.S)
    body = m.group(0)
    assert body.index("usePlayerOpen(") < body.index("if (!clip) return null;"), \
        "usePlayerOpen sits below the early return — the hook order is unstable"


# ── what the first fix got right, kept ───────────────────────────────────────

_NO_BLUR_OVER_VIDEO = ("rd-modal-bg", "rd-modal-close", "rd-scorebadge", "rd-viralbadge")


def _css_rule(selector: str) -> str:
    m = re.search(r"\." + selector + r"\{(.*?)\}", CSS, re.S)
    assert m, f".{selector} rule not found"
    return m.group(1)


@pytest.mark.parametrize("selector", _NO_BLUR_OVER_VIDEO)
def test_nothing_covering_the_player_blurs_even_when_it_is_not_playing(selector):
    """These four sit ON the player rather than behind it, so unlike everything
    else they must not carry a blur AT ALL — not merely while playing. The
    hz-player class cannot help here: the badges are visible over the video the
    whole time it is up."""
    assert "backdrop-filter" not in _css_rule(selector), (
        f".{selector} has a backdrop-filter. It covers the clip player, so the "
        f"browser re-blurs that patch of video on every decoded frame.")


@pytest.mark.parametrize("selector", _NO_BLUR_OVER_VIDEO)
def test_the_removed_blur_was_paid_for_with_opacity(selector):
    """The blur was doing real visual work — separating the badges from a bright
    thumbnail, and dimming the page behind the modal. Dropping it without
    raising the background opacity would leave white-on-pale text."""
    rule = _css_rule(selector)
    m = re.search(r"background:rgba\(\d+,\s*\d+,\s*\d+,\s*([\d.]+)\)", rule)
    assert m, f".{selector} has no rgba background to carry the contrast"
    assert float(m.group(1)) >= 0.8, (
        f".{selector} is only {m.group(1)} opaque with no blur behind it — "
        f"the text will not hold up over a bright clip thumbnail")


def test_the_scrims_are_opaque_enough_that_losing_the_blur_is_invisible():
    """The whole argument for switching the glass off is that nobody can see it
    through the scrim. If a scrim were made more transparent, the page behind
    would start showing through unblurred and the change would become visible."""
    for selector in ("rd-modal-bg", "ed-bg"):
        rule = _css_rule(selector)
        m = re.search(r"background:rgba\(\d+,\s*\d+,\s*\d+,\s*([\d.]+)\)", rule)
        assert m and float(m.group(1)) >= 0.85, (
            f".{selector} is no longer opaque enough to hide the page behind "
            f"it, so dropping the blur there would now be visible")
