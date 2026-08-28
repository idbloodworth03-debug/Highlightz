"""Phase 1a: the token foundation, and the things it must not have broken.

WHAT THIS PINS. A design refactor is only safe if the invariants are asserted
somewhere — otherwise the next edit quietly reintroduces the thing that was
just removed. These are the invariants Phase 1a establishes.
"""

import re

import pytest

PAGES = ("landing", "dashboard", "tutorial", "compare")
SCALE = {0, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128}
SPACE_PROPS = r"(?:margin|padding|gap|row-gap|column-gap)(?:-(?:top|right|bottom|left))?"


def css(page: str) -> str:
    from src.dashboard.api import LANDING_HTML
    from src.dashboard.aurora_html import DASHBOARD_HTML
    from src.dashboard import tutorial_html, compare_html
    html = {"landing": LANDING_HTML, "dashboard": DASHBOARD_HTML,
            "tutorial": tutorial_html.render(), "compare": compare_html.render()}[page]
    return "\n".join(re.findall(r"<style>(.*?)</style>", html, re.S))


# ── the spacing scale ────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", PAGES)
def test_every_spacing_value_is_on_the_scale(page):
    """71% of 932 declarations were off any scale before this. Nothing gets an
    arbitrary number again."""
    off = []
    for m in re.finditer(SPACE_PROPS + r"\s*:\s*([^;}\n]+)", css(page)):
        for tok in m.group(1).split():
            mm = re.fullmatch(r"(-?\d+(?:\.\d+)?)px", tok)
            if mm and abs(float(mm.group(1))) not in SCALE:
                off.append(tok)
    assert not off, f"{page}: {len(off)} off-scale values, e.g. {sorted(set(off))[:8]}"


@pytest.mark.parametrize("page", PAGES)
def test_no_hairline_spacing_was_collapsed_to_zero(page):
    """THE MISTAKE THIS CAUGHT. The first snap had ties going down with no
    floor, which sent 41 instances of 2px and 6 of 1px to 0 — that does not
    tighten spacing, it deletes it, turning padding on badges and pills into
    none. Any non-zero value under 4px is a deliberate hairline and belongs at
    the bottom step, never at nothing."""
    c = css(page)
    for prop in ("padding", "margin", "gap"):
        assert not re.search(prop + r"\s*:\s*0px\s+0px\s+0px\s+0px", c), \
            f"{page}: a four-sided {prop} collapsed to zero"


# ── the type scale ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", PAGES)
def test_no_fractional_font_sizes(page):
    """Nine fractional sizes (9.5 … 16.5, 12.8) produced 285 elements on the
    landing page with a non-integer computed size. A fractional size never
    lands on a device pixel, which is most of why this read as slightly-off."""
    frac = re.findall(r"font-size\s*:\s*(\d+\.\d+)px", css(page))
    assert not frac, f"{page}: fractional font sizes back: {sorted(set(frac))}"


@pytest.mark.parametrize("page", PAGES)
def test_font_weights_are_real_steps(page):
    """`650` is not a step in any scale."""
    weights = {w for w in re.findall(r"font-weight\s*:\s*(\d+)", css(page))}
    allowed = {"400", "500", "600", "700", "800", "100", "900"}
    assert weights <= allowed, f"{page}: odd weights {weights - allowed}"


# ── the unified palette ──────────────────────────────────────────────────────

RETIRED = {
    "#08080b": "#0e0b11", "#f6f6f9": "#f2eaf7", "#9c9caa": "#b9aec4",
    "#5d5d6b": "#9c90a6", "#c79bff": "#c489e4", "#a855f7": "#b86adc",
}


@pytest.mark.parametrize("page", PAGES)
def test_the_product_and_the_site_use_one_palette(page):
    """The dashboard and the marketing pages used different near-duplicate
    hexes for the same six roles — up to dE 31 apart, which is not a duplicate,
    it is a different colour. A user clicking through from the site watched the
    purple change."""
    c = css(page).lower()
    back = [old for old in RETIRED if old in c]
    assert not back, f"{page}: retired hexes reappeared: {back}"


def test_the_purples_were_mapped_by_role_not_by_brightness():
    """--acc is the LIGHT purple used on text, so it became --glow-ink
    (#c489e4, dE 8.8) — the marketing token that exists for that job — not
    --flare (#d26afb, dE 31.2), which is a fill colour."""
    c = css("dashboard").lower()
    assert "#c489e4" in c, "the text purple is not the role-matched one"
    assert "--acc: #c489e4" in c or "--acc:#c489e4" in c


def test_gold_survived_the_orange_collapse():
    """THE SECOND MISTAKE THIS CAUGHT. Nine oranges were collapsed to two on
    the reading that they all did one job. They did not: the crowd-suggestion
    badge is gold (#ffd45e/#ff9d00) and was deliberately made unlike the viral
    badge beside it. The collapse merged them — dE 10.9 and 18.8 apart, which
    the eye reads. Gold is an identity, not a warning, so it kept its own name."""
    c = css("dashboard").lower()
    assert "#ffd45e" in c and "#ff9d00" in c, "the gold badge was merged into warn"
    assert "--sug:" in c, "gold has no name of its own"


# ── fonts ────────────────────────────────────────────────────────────────────

def test_the_dashboard_self_hosts_its_font():
    """It loaded Inter with an @import of Google Fonts from inside <style> —
    the slowest way to load a face (the sheet must parse before the request
    starts) and a third-party request on every dashboard load, while the
    marketing pages self-host all four of theirs."""
    c = css("dashboard")
    assert "fonts.googleapis.com" not in c, "the Google Fonts @import is back"
    assert "@font-face" in c and "/static/fonts/inter-var.woff2" in c
    assert "font-display:swap" in c.replace(" ", "")


def test_the_font_file_ships():
    from pathlib import Path
    from src.dashboard.api import _STATIC_DIR
    f = Path(_STATIC_DIR) / "fonts" / "inter-var.woff2"
    assert f.exists(), "inter-var.woff2 is referenced but not shipped"
    assert f.read_bytes()[:4] == b"wOF2", "not a woff2 file"


# ── motion ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", ("landing", "dashboard"))
def test_motion_tokens_exist(page):
    c = css(page)
    for tok in ("--ease:", "--dur-fast:", "--dur-slow:", "--dur-event:"):
        assert tok in c.replace(" ", ""), f"{page}: missing {tok}"


def test_nothing_transitions_a_layout_property():
    """`all`, `left` and `width` are not composited — the browser relayouts
    every frame. transform and opacity are the only two that are free."""
    c = css("dashboard")
    for bad in (r"transition:\s*all\b", r"transition:\s*left\b", r"transition:\s*width\b"):
        assert not re.search(bad, c), f"a layout property is being transitioned: {bad}"


def test_the_score_wall_reserve_is_used_once():
    """--s-10 exists for the score wall and nothing else. If it appears twice
    outside its own definition, the second use is wrong."""
    c = css("landing")
    uses = len(re.findall(r"var\(--s-10\)", c))
    assert uses <= 1, f"--s-10 used {uses} times; it is reserved for the wall"


# ── the decoration removed this phase ────────────────────────────────────────

def test_the_final_cta_bloom_is_gone():
    """One decorative element per phase. This was a 320px blurred purple radial
    floating behind the closing heading — the house style of every
    AI-generated hero, and the band already changes surface tone here."""
    c = css("landing")
    m = re.search(r"\.final::before\{[^}]*\}", c)
    assert not m, "the bloom is back"
