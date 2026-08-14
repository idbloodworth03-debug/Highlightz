"""The landing page's palette, checked as numbers rather than as vibes.

Dark themes fail WCAG constantly and the failure is invisible in a screenshot —
low-contrast purple-on-purple looks "moody" right up until someone cannot read
it. These tests recompute the real contrast ratios from the tokens declared in
`:root`, so a future colour tweak that drops muted copy under AA fails here
instead of shipping.

They also pin the two decisions that make this purple ours rather than Twitch's:
where it sits in hue/saturation, and that it is used as LIGHT (rims, strokes,
glows) rather than as the fill of every control.
"""

import colorsys
import re

import pytest

from src.dashboard import api

CSS = api.LANDING_HTML


def _tokens() -> dict[str, str]:
    """Tokens as declared at :root — i.e. the LIGHT-surface values."""
    root = CSS[CSS.index(":root{"):CSS.index("}", CSS.index(":root{"))]
    return {m.group(1): m.group(2) for m in re.finditer(r"--([\w-]+):(#[0-9A-Fa-f]{6})", root)}


def _dark_tokens() -> dict[str, str]:
    """The SAME token names, re-declared for dark panels.

    The page is two surfaces now: a bone page with dark instrument panels. The
    ink tokens are redeclared inside the dark-context selector so that the
    hundred-odd existing rules using var(--ink-3) resolve correctly in both
    worlds. Both sets have to pass contrast, against their own surfaces — a
    palette that is only checked on one of them is half-checked."""
    i = CSS.index(".band-dark,.panel,.demo,.shot-frame,.exl-card{")
    block = CSS[i:CSS.index("}", i)]
    out = {m.group(1): m.group(2) for m in re.finditer(r"--([\w-]+):(#[0-9A-Fa-f]{6})", block)}
    assert out, "the dark-context ink block is gone — dark panels now inherit light ink"
    return out


def _rgb(hexstr: str) -> tuple[int, int, int]:
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(hexstr: str) -> float:
    def chan(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = _rgb(hexstr)
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _hsl(hexstr: str) -> tuple[float, float, float]:
    r, g, b = (c / 255 for c in _rgb(hexstr))
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, s * 100, l * 100


DARK_SURFACES  = ("void", "wall", "bruise")
LIGHT_SURFACES = ("bone", "sand")


def test_every_token_is_declared():
    t = _tokens()
    for name in ("void", "wall", "bruise", "glow", "glow-ink", "flare", "ember",
                 "ink", "ink-2", "ink-3", "bone", "sand", "plum", "ember-ink", "iris"):
        assert name in t, f"--{name} is gone from :root"


@pytest.mark.parametrize("ink", ["ink", "ink-2", "ink-3"])
@pytest.mark.parametrize("surface", LIGHT_SURFACES)
def test_light_surface_copy_passes_aa(ink, surface):
    """The page is bone now, and muted text on a LIGHT background is the
    classic place a light theme fails — it looks tasteful and is unreadable."""
    t = _tokens()
    ratio = contrast(t[ink], t[surface])
    assert ratio >= 4.5, f"--{ink} on --{surface} is {ratio:.2f}:1, under AA"


@pytest.mark.parametrize("ink", ["ink", "ink-2", "ink-3"])
@pytest.mark.parametrize("surface", DARK_SURFACES)
def test_body_and_muted_copy_pass_aa_on_every_surface(ink, surface):
    """Muted labels are where a dark PANEL fails. --ink-3 is the dimmest text
    on a panel and it has to clear 4.5:1 on every panel shade it can land on.

    Reads the dark-context re-declarations, not :root — since the inversion,
    :root carries the light-surface values and the panels redeclare the same
    names. Checking :root here would test bone-coloured ink against a bone
    background and pass while telling you nothing."""
    t = _tokens()
    d = _dark_tokens()
    ratio = contrast(d[ink], t[surface])
    assert ratio >= 4.5, f"--{ink} on a dark panel (--{surface}) is {ratio:.2f}:1, under AA"


@pytest.mark.parametrize("surface", DARK_SURFACES)
def test_the_glow_is_bright_enough_to_be_a_visible_edge(surface):
    """--glow draws rims, strokes and chart lines — non-text UI, so the floor is
    3:1. It is deliberately NOT held to 4.5, because holding a light source to
    body-text contrast is what turns a glow into a paint."""
    t = _tokens()
    r = contrast(t["glow"], t[surface])
    assert r >= 3.0, f"--glow on --{surface} is {r:.2f}:1 — the rim would vanish"


@pytest.mark.parametrize("surface", DARK_SURFACES)
def test_purple_that_has_to_be_READ_uses_the_lighter_step(surface):
    """--glow at 4.32:1 on --bruise is below AA, which is exactly the
    purple-on-purple trap. That is why there is a second step: any purple set as
    small text uses --glow-ink, and it must clear AA everywhere."""
    t = _tokens()
    r = contrast(t["glow-ink"], t[surface])
    assert r >= 4.5, f"--glow-ink on --{surface} is {r:.2f}:1, below AA"


def test_the_purple_is_ours_and_not_twitch_s():
    """Twitch's own purples sit at H264 / S100. Ours is pushed toward orchid and
    held well below full saturation, because light landing on a matte wall in a
    dark room desaturates — a 100%-saturated purple reads as paint, not glow.
    """
    h, s, l = _hsl(_tokens()["glow"])
    assert 275 <= h <= 290, f"--glow drifted to hue {h:.0f}; Twitch's lane is 264"
    assert s <= 75, f"--glow is {s:.0f}% saturated — that is paint, not light"
    assert 55 <= l <= 72, f"--glow lightness {l:.0f}% will not read as a light source"

    for twitch in ("#9146FF", "#A970FF"):
        assert twitch.lower() not in CSS.lower(), f"{twitch} is back in the palette"


def test_the_warm_counterpoint_is_actually_warm_and_actually_used():
    """--ember is what stops this reading as generic purple SaaS, and it earns
    its place by having a JOB: it is the below-threshold state of every score on
    the page, so the room is lamp-lit until a trigger fires."""
    h, s, _ = _hsl(_tokens()["ember"])
    assert 20 <= h <= 50, f"--ember is at hue {h:.0f} — not a warm lamp"
    assert s >= 70, "--ember is too grey to read as a light"
    assert CSS.count("var(--ember)") >= 12, "the counterpoint is barely used"
    # It is the resting state of the two live score readouts.
    for sel in (".trig-v", ".demo-score span"):
        block = CSS[CSS.index(sel + "{"):CSS.index("}", CSS.index(sel + "{"))]
        assert "var(--ember)" in block, f"{sel} should rest at the lamp colour"
    for sel in (".trig.hot .trig-v", ".demo.hot .demo-score span"):
        block = CSS[CSS.index(sel + "{"):CSS.index("}", CSS.index(sel + "{"))]
        assert "var(--flare)" in block, f"{sel} should snap to the hot accent"


def test_surfaces_are_warm_not_blue_black():
    """The brief this was built to is explicit: plum-black, not slate/navy. A
    hue in the blue quadrant means someone reached for a stock dark-mode grey."""
    t = _tokens()
    for name in DARK_SURFACES:
        h, _, _ = _hsl(t[name])
        assert 255 <= h <= 300, f"--{name} is at hue {h:.0f} — that is a blue-black"


def test_purple_is_light_not_paint():
    """Exactly ONE control on this page is a solid purple: the primary CTA.

    This test used to assert the opposite — that no control is a purple fill,
    because on the old dark page a rim-lit surface read beautifully. On bone it
    read as nothing at all: the first render of the light theme had an
    invisible primary button. So the rule is inverted DELIBERATELY, and
    narrowed rather than dropped, because the thing it was protecting is still
    worth protecting — a page where every control is a purple slab is every
    other tool in this category.
    """
    key = CSS[CSS.index(".btn-key{"):CSS.index("}", CSS.index(".btn-key{"))]
    assert "linear-gradient(168deg,#7B3A9E,#5B2472)" in key, \
        "the primary CTA should be a solid plum on the light page"
    # And it is the ONLY one. The quiet button stays a surface.
    quiet = CSS[CSS.index(".btn-quiet{"):CSS.index("}", CSS.index(".btn-quiet{"))]
    assert "var(--bone)" in quiet and "#7B3A9E" not in quiet, \
        "the secondary button became a purple fill too — that is the slab page"


def test_the_signature_is_wired_to_one_number():
    """The trigger score is the light in the room: a single --lit custom property
    that the nav rim, the hero light and the demo panel all read from. If any of
    them stops consuming it, the page still animates but the ROOM stops
    responding — which is the entire idea.
    """
    assert "--lit:0" in CSS.replace(" ", ""), "--lit is not declared"
    consumers = CSS.count("var(--lit)")
    assert consumers >= 4, f"only {consumers} things react to the trigger score"
    # .nav::after, not .nav — the reactive hairline moved to the pseudo element
    # when the bar itself became bone glass. .thread-fill is the through-line,
    # the newest and most visible consumer of the same number.
    for sel in (".nav::after{", ".demo-wrap::before{", ".demo{", ".trig{", ".thread-fill{"):
        block = CSS[CSS.index(sel):CSS.index("}", CSS.index(sel))]
        assert "var(--lit)" in block, f"{sel[:-1]} no longer reacts to the score"
    # And it is written from the loop, throttled to changes rather than frames.
    assert "root.style.setProperty('--lit'" in CSS
    assert "if(q!==lastLit)" in CSS, "--lit is being written on every frame"
