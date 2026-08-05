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
    root = CSS[CSS.index(":root{"):CSS.index("}", CSS.index(":root{"))]
    return {m.group(1): m.group(2) for m in re.finditer(r"--([\w-]+):(#[0-9A-Fa-f]{6})", root)}


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


SURFACES = ("void", "wall", "bruise")


def test_every_token_is_declared():
    t = _tokens()
    for name in ("void", "wall", "bruise", "glow", "glow-ink", "flare", "ember",
                 "ink", "ink-2", "ink-3"):
        assert name in t, f"--{name} is gone from :root"


@pytest.mark.parametrize("ink", ["ink", "ink-2", "ink-3"])
@pytest.mark.parametrize("surface", SURFACES)
def test_body_and_muted_copy_pass_aa_on_every_surface(ink, surface):
    """Muted labels are where dark themes actually fail. --ink-3 is the dimmest
    text on the page and it has to clear 4.5:1 on the DARKEST-to-LIGHTEST range
    of surfaces it can land on, not just on the page background.

    The page it replaced used #5d5d6b for captions on #08080b — 3.09:1, a real
    failure that shipped because it looked fine.
    """
    t = _tokens()
    r = contrast(t[ink], t[surface])
    assert r >= 4.5, f"--{ink} on --{surface} is {r:.2f}:1, below AA for body text"


@pytest.mark.parametrize("surface", SURFACES)
def test_the_glow_is_bright_enough_to_be_a_visible_edge(surface):
    """--glow draws rims, strokes and chart lines — non-text UI, so the floor is
    3:1. It is deliberately NOT held to 4.5, because holding a light source to
    body-text contrast is what turns a glow into a paint."""
    t = _tokens()
    r = contrast(t["glow"], t[surface])
    assert r >= 3.0, f"--glow on --{surface} is {r:.2f}:1 — the rim would vanish"


@pytest.mark.parametrize("surface", SURFACES)
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
    for name in SURFACES:
        h, _, _ = _hsl(t[name])
        assert 255 <= h <= 300, f"--{name} is at hue {h:.0f} — that is a blue-black"


def test_purple_is_light_not_paint():
    """No control on this page is a purple FILL. Buttons are surfaces in the
    room with a violet rim; the moment one becomes a solid purple slab, the
    whole conceit collapses into every other tool in this category.
    """
    key = CSS[CSS.index(".btn-key{"):CSS.index("}", CSS.index(".btn-key{"))]
    # The face is the bruised surface; the flare only appears in the border-box
    # layer, which is the 1px rim.
    face, _, rim = key.partition("border-box")
    assert "var(--bruise)" in face, "the primary button's face should be a surface"
    assert "padding-box" in face and "var(--flare)" in face.split("padding-box")[1], \
        "the hot accent must live in the rim layer, not the fill"

    # And the banned gradients never come back.
    lowered = CSS.lower()
    for banned in ("#f943ff", "#7c6bff", "#a855f7"):
        assert banned not in lowered, f"the old {banned} gradient stop is back"
    assert "background-clip:text" not in lowered.replace(" ", ""), "gradient text is back"
    assert "-webkit-text-fill-color:transparent" not in lowered, "gradient text is back"


def test_the_signature_is_wired_to_one_number():
    """The trigger score is the light in the room: a single --lit custom property
    that the nav rim, the hero light and the demo panel all read from. If any of
    them stops consuming it, the page still animates but the ROOM stops
    responding — which is the entire idea.
    """
    assert "--lit:0" in CSS.replace(" ", ""), "--lit is not declared"
    consumers = CSS.count("var(--lit)")
    assert consumers >= 4, f"only {consumers} things react to the trigger score"
    for sel in (".nav{", ".demo-wrap::before{", ".demo{", ".trig{"):
        block = CSS[CSS.index(sel):CSS.index("}", CSS.index(sel))]
        assert "var(--lit)" in block, f"{sel[:-1]} no longer reacts to the score"
    # And it is written from the loop, throttled to changes rather than frames.
    assert "root.style.setProperty('--lit'" in CSS
    assert "if(q!==lastLit)" in CSS, "--lit is being written on every frame"
