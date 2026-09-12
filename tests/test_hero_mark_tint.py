"""
The hero mark is hot pink, and nothing else moved.

WHY THIS IS A TEST AND NOT JUST A COLOUR. /static/logo-mark.png is a SHARED
asset — the dashboard nav, the favicon, the email signature and the landing
hero all point at the same file. Recolouring it by editing the PNG would have
repainted every one of them. The tint therefore lives on one CSS rule, and the
thing worth pinning is that it stayed there.

The second test is the subtle one. `filter` is a single property, so the
phone-width rule naming `blur()` REPLACES the desktop value outright rather
than adding to it. Miss that and the mark is pink on a laptop and purple on a
phone — a split nobody sees unless they check both.
"""

import re

from src.dashboard import api


def _no_comments(css: str) -> str:
    """Drop /* ... */ so prose about the tint is not mistaken for the tint.

    The rules carry comments explaining why the rotation is repeated at the
    phone breakpoint, and those comments name `hue-rotate`. Counting raw text
    would read them as two more tinted selectors.
    """
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _cover_rules(css: str):
    return re.findall(r"\.cover-bg img\{[^}]*\}", _no_comments(css))


def test_the_hero_mark_is_tinted():
    rules = _cover_rules(api.LANDING_HTML)
    assert rules, "the hero mark rule is gone"
    assert any("hue-rotate" in r for r in rules), "the hero mark is not tinted"


def test_every_breakpoint_tints_it_the_same():
    """`filter` is one property. A breakpoint that re-declares blur without the
    rotation silently drops it for that screen size."""
    rules = _cover_rules(api.LANDING_HTML)
    assert len(rules) >= 2, "expected a desktop rule and a phone override"
    for r in rules:
        assert "filter:" in r, r
        assert "hue-rotate(40deg)" in r, \
            f"a breakpoint sets filter without the tint, so the mark changes colour there: {r}"


def test_the_shared_logo_file_is_not_recoloured():
    """The tint is CSS on one element. If it ever moves into the PNG, the nav,
    the favicon and the signature all turn pink with it."""
    from PIL import Image
    img = Image.open("src/dashboard/static/logo-mark.png").convert("RGB")
    px = [p for p in img.getdata() if sum(p) > 90]
    assert px, "the mark is empty"
    # Brand violet/magenta has blue >= red across the mark. A pink or red file
    # would invert that on most pixels.
    bluer = sum(1 for r, g, b in px if b >= r)
    assert bluer / len(px) > 0.5, \
        "logo-mark.png itself looks recoloured — that would repaint every use of it"


def test_nothing_else_on_the_landing_page_was_tinted():
    """'Only this one.' A hue-rotate that escaped onto a shared selector would
    swing the whole page.

    Counted rather than matched by selector: the phone rule is nested inside an
    @media block, so a naive "what precedes this brace" regex reports the media
    query as the selector and the check looks broken when it is not. Comparing
    totals sidesteps the nesting entirely.
    """
    css = api.LANDING_HTML
    total = _no_comments(css).count("hue-rotate(")
    on_the_mark = sum(r.count("hue-rotate(") for r in _cover_rules(css))
    assert total == on_the_mark, (
        f"{total - on_the_mark} hue-rotate(s) are outside the hero mark rule")
