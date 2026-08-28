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


# ── phase 1b: the inline styles ──────────────────────────────────────────────

def _inline_objects() -> list[str]:
    """Every style={{…}} body in the dashboard, brace-matched.

    Not a regex over the file: an inline object can contain nested braces
    (ternaries, template literals), and a naive `\\{\\{(.*?)\\}\\}` stops at the
    first one and hands back half a declaration.
    """
    import src.dashboard.aurora_html as mod
    from pathlib import Path
    src = Path(mod.__file__).read_text(encoding="utf-8")
    out, i = [], 0
    while True:
        j = src.find("style={{", i)
        if j < 0:
            return out
        k, d = j + 7, 0
        while k < len(src):
            if src[k] == "{":
                d += 1
            elif src[k] == "}":
                d -= 1
                if d == 0:
                    break
            k += 1
        out.append(src[j + 8:k])
        i = k


_INLINE_SPACE = {"padding", "margin", "gap", "paddingTop", "paddingBottom",
                 "paddingLeft", "paddingRight", "marginTop", "marginBottom",
                 "marginLeft", "marginRight", "rowGap", "columnGap"}
# `(?:,|\Z)` — BOTH terminators. An earlier version of this check required a
# following comma, which meant the LAST declaration in every object was invisible
# to it. The transform it was checking had the same bug, so the check passed by
# sharing it and 77 off-scale values survived. A verification that can only see
# what the transform saw is not a verification.
_INLINE_DECL = (r"\b(\w+)\s*:\s*('[-\d.px ]*'|\"[-\d.px ]*\"|-?\d+(?:\.\d+)?)"
                r"(?=\s*(?:,|\Z))")


def test_inline_spacing_is_on_the_scale():
    """1069 declarations across 361 objects that no CSS refactor can reach —
    React writes them camelCase with bare numbers, so `padding:26` is 26px and
    Phase 1a's stylesheet regex correctly never saw them."""
    bad = []
    for o in _inline_objects():
        for m in re.finditer(_INLINE_DECL, o):
            if m.group(1) in _INLINE_SPACE:
                for v in re.findall(r"-?\d+(?:\.\d+)?", m.group(2)):
                    if abs(float(v)) not in SCALE:
                        bad.append(f"{m.group(1)}:{v}")
    assert not bad, f"{len(bad)} off-scale inline spacing values: {sorted(set(bad))[:10]}"


def test_inline_font_sizes_are_on_the_scale():
    steps = {12, 14, 16, 17, 24, 30, 44}
    bad = []
    for o in _inline_objects():
        for m in re.finditer(_INLINE_DECL, o):
            if m.group(1) == "fontSize":
                for v in re.findall(r"-?\d+(?:\.\d+)?", m.group(2)):
                    if float(v) not in steps:
                        bad.append(v)
    assert not bad, f"off-scale inline font sizes: {sorted(set(bad))}"


def test_the_last_declaration_in_an_object_is_actually_checked():
    """Guards the guard. If _INLINE_DECL stops requiring a trailing comma to be
    optional, every check above silently stops seeing final declarations — the
    exact hole that let 77 values through."""
    probe = "color:'red',marginTop:14"
    found = [m.group(1) for m in re.finditer(_INLINE_DECL, probe)]
    assert "marginTop" in found, "the pattern cannot see a trailing declaration"


def test_no_off_palette_colours_hide_in_inline_styles():
    """These were invisible to every stylesheet analysis because they existed
    only inside style={{…}}: a mint green, an indigo, two reds and a tenth
    amber, none of them in the palette, each doing a job a token already
    covers."""
    gone = ("#86efac", "#a5b4fc", "#22c55e", "#f87171", "#fca5a5", "#ffc53d", "#15111f")
    body = " ".join(_inline_objects()).lower()
    back = [h for h in gone if h in body]
    assert not back, f"off-palette colours returned: {back}"


def test_the_twitch_purple_is_spelled_one_way():
    """It was written #9146ff twice and #9147ff twice — one digit apart, in a
    brand colour that has exactly one correct value."""
    import src.dashboard.aurora_html as mod
    from pathlib import Path
    src = Path(mod.__file__).read_text(encoding="utf-8").lower()
    assert "#9147ff" not in src, "the mistyped Twitch purple is back"
    assert "#9146ff" in src


def test_inline_styles_prefer_tokens_over_palette_literals():
    """A palette colour written longhand inline cannot follow the palette. Only
    non-palette literals are allowed to remain — pure black and white, the
    Twitch brand purple, the Kick theme green, and three gradient stops."""
    allowed = {"#fff", "#000", "#9146ff", "#53fc18", "#2a1840", "#3a1a4d", "#14021c"}
    found = set()
    for o in _inline_objects():
        found |= {h.lower() for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", o)}
    assert found <= allowed, f"palette colours still written longhand inline: {found - allowed}"
