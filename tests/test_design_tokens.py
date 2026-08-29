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


def test_the_suggestion_identity_kept_its_own_name():
    """THE SECOND MISTAKE THIS CAUGHT, updated. Nine oranges were collapsed to
    two on the reading that they all did one job; the crowd-suggestion badge
    was deliberately unlike the viral badge beside it and the collapse merged
    them. The badge has since been recoloured purple ("Highlight") on the
    owner's call — the invariant that survives is the NAME: the identity lives
    in its own --sug token, never folded into --pending/--warn or borrowed
    from --acc, so the next palette collapse cannot merge it either."""
    c = css("dashboard").lower()
    assert "--sug:" in c and "--sug-deep:" in c, "the identity has no name of its own"
    assert "#ffd45e" not in c and "#ff9d00" not in c, \
        "the retired gold is still in the sheet somewhere"


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


# ── phase 2: the score wall ──────────────────────────────────────────────────

def test_the_threshold_is_the_strongest_hairline_not_the_faintest():
    """It was rgba(242,234,247,.18) in a 3/6 dash — the faintest mark in the
    tile, and the line the entire product is about. A crossing had to be
    inferred from the numeral because the thing being crossed was barely there."""
    c = css("landing")
    rule = re.search(r"\.tile-thline\{([^}]*)\}", c).group(1)
    assert "rgba(196,137,228" in rule, "the datum is not drawn in the accent"
    assert "dasharray:none" in rule.replace(" ", ""), "the datum is dashed again"
    alpha = float(re.search(r"rgba\(196,137,228,([\d.]+)\)", rule).group(1))
    assert alpha >= 0.4, f"the datum is back to {alpha} opacity"


def test_the_threshold_reading_sits_at_the_line():
    """`thr 71` used to be in the tile header, about a hundred pixels above the
    mark it names."""
    c = css("landing")
    assert ".tile-thmark{" in c
    from src.dashboard.api import LANDING_HTML
    assert "thmark.style.top" in LANDING_HTML, "the reading is not positioned at the line"
    assert "e.thmark.textContent='thr '+tl.thresh" in LANDING_HTML


def test_the_crossing_is_drawn_not_inferred():
    """The trace was one stroke for the whole nine-second window, so the moment
    that matters looked identical to the moment before it."""
    c = css("landing")
    over = re.search(r"\.tile-line-over\{([^}]*)\}", c).group(1)
    base = re.search(r"\.tile-line\{([^}]*)\}", c).group(1)
    ow = float(re.search(r"stroke-width:([\d.]+)", over).group(1))
    bw = float(re.search(r"stroke-width:([\d.]+)", base).group(1))
    assert ow > bw, "the above-threshold trace is not heavier than the quiet half"
    assert "var(--flare)" in over and "var(--glow)" in base, "both halves are the same colour"


def test_the_bright_half_is_clipped_to_this_channels_own_threshold():
    """One shared line across all four tiles would have been a lie — the four
    thresholds are genuinely different (71/68/70/67) and yFor() puts them at
    different heights on a shared 0-100 axis. Each tile clips its own."""
    from src.dashboard.api import LANDING_HTML
    assert "clipPath" in LANDING_HTML and "thclip" in LANDING_HTML
    assert "e.clipRect.setAttribute('height',y)" in LANDING_HTML, \
        "the clip is not tied to this tile's own threshold y"
    assert "e.lineOver.setAttribute('d',d)" in LANDING_HTML, \
        "the two halves have drifted onto different paths — a seam at the crossing"


def test_the_wall_gets_the_only_rhythm_break_and_not_from_its_own_height():
    """The first attempt put --s-10 as padding INSIDE .hero-stack. The hero is
    min-height:100svh with the stack in a minmax(0,1fr) row, so the padding came
    straight out of the wall: it lost 97px. Air below a fixed-height box has to
    be added outside it."""
    c = css("landing")
    stack = re.search(r"\.hero-stack\{([^}]*)\}", c).group(1)
    assert "--s-10" not in stack, "the break is inside the hero again, eating the wall"
    band = re.search(r"\.hero\.hero-band\{([^}]*)\}", c).group(1)
    assert "margin-bottom:var(--s-10)" in band, "the wall lost its breathing room"
    # A margin and not padding, for the same reason it was never padding: the
    # hero is min-height:100svh, so padding is taken from the wall's own row.
    assert not re.search(r"padding-(?:bottom|top):var\(--s-10\)", band), \
        "the break is padding again, so it comes out of the wall"


def test_the_chart_takes_the_tiles_slack():
    """It was a fixed height pinned with margin-top:auto, so every spare pixel
    opened as a gap between the readout and the top of the chart."""
    c = css("landing")
    rule = re.search(r"\.tile-chart\{([^}]*)\}", c).group(1)
    assert "flex:1 1 auto" in rule, "the chart no longer absorbs the slack"
    assert "margin-top:auto" not in rule, "the chart is pinned to the bottom again"


def test_the_dead_stage_stylesheet_stayed_dead():
    """This used to pin overflow:hidden on .stage-media (the scale(1.1) wash
    bled 1.2px past a 375 viewport). The stage was then removed from the wall
    entirely, and its ~100 lines of orphaned CSS were removed with it — dead
    rules are not harmless here: a dead FAQ stylesheet once silently overrode
    the live one's measure. So the guard inverted: no .stage- rule may exist
    without stage markup to style."""
    c = css("landing")
    if ".stage-" in c:
        from src.dashboard.api import LANDING_HTML
        assert 'class="stage' in LANDING_HTML, \
            "stage CSS exists but no stage markup does — the dead block is back"


def test_the_ambient_hot_glow_is_gone():
    """Decoration removed this phase. It was the only way to tell a tile was
    near its threshold back when the line was invisible; the datum and the
    two-tone trace now say it precisely. .tile.fire keeps its glow — that marks
    an event, not a proximity."""
    c = css("landing")
    assert not re.search(r"\.tile\.hot\{[^}]*box-shadow", c), "the ambient glow is back"
    assert re.search(r"\.tile\.fire\{[^}]*box-shadow", c), "the fire lost its glow"


# ── phase 3: landing page rhythm ─────────────────────────────────────────────

def test_every_section_boundary_is_the_same():
    """The sequence used to be 112 / 112 / 96 / 80, and the two 112s read as
    hesitations in the scroll. Each section contributes the same half-gap, so
    every boundary between two sections is exactly --s-9."""
    c = css("landing")
    m = re.search(r"section\{padding-top:var\(--s-(\d+)\);padding-bottom:var\(--s-(\d+)\)\}", c)
    assert m, "the section rule is no longer written in tokens"
    assert m.group(1) == m.group(2), "sections are asymmetric again"


def test_section_emphasis_moved_inside_rather_than_disappearing():
    """The prior design deliberately gave the argument and the decision more
    room than the reference material, and that judgement is worth keeping. It
    just cannot be paid for out of the boundaries."""
    c = css("landing").replace("\n", "").replace("  ", "")
    gaps = dict(re.findall(r"#(how|pricing|faq) \.sec-title\{margin-bottom:var\(--s-(\d+)\)", c))
    assert set(gaps) == {"how", "pricing", "faq"}
    assert int(gaps["how"]) > int(gaps["faq"]), \
        "the argument no longer gets more room than the reference material"


def test_no_prose_runs_past_the_readable_band():
    """Fourteen blocks ran over 75 characters, the worst at 144. Every one was
    a width problem — not a word of copy changed."""
    c = css("landing")
    assert "--measure:52ch" in c.replace(" ", ""), "the measure token moved"
    for sel in (".faq-a", ".faq-more", ".price-tiny", ".price-lead", ".feat p", ".feat-wide p"):
        rule = re.search(re.escape(sel) + r"\{([^}]*)\}", c)
        assert rule and "var(--measure)" in rule.group(1), f"{sel} lost its measure"


def test_the_measure_is_calibrated_to_real_characters_not_to_ch():
    """`ch` is the width of the digit zero, which in Sora is 9.17px at 14px
    against an average character of 6.9px. The plan's 68ch therefore allowed 90
    characters — thirteen blocks stayed too wide while appearing to be capped.
    52ch is the value that actually lands inside the band."""
    c = css("landing").replace(" ", "")
    val = int(re.search(r"--measure:(\d+)ch", c).group(1))
    assert 46 <= val <= 58, f"--measure is {val}ch; outside the calibrated range"


def test_the_pricing_ladder_survived_but_the_buttons_share_a_baseline():
    """The ladder used to step width, radius, price size, border and glow. The
    tiers are hairline cells now — same construction as the wall and the stats
    band — so radius and border are gone BY DESIGN, and the ladder lives in
    what is left: unequal column widths and the price stepping 30 -> 44, with
    Pro carrying the wash and the bright top edge. The buttons still share a
    baseline because every tier keeps the same bottom padding."""
    c = css("landing")
    tiers = re.search(r"\.ptiers\{([^}]*)\}", c).group(1)
    cols = re.search(r"grid-template-columns:([^;]+)", tiers).group(1)
    fracs = re.findall(r"minmax\(0,([\d.]+)fr\)", cols)
    assert len(set(fracs)) == 3, f"the ladder was flattened to equal columns: {fracs}"
    base = re.search(r"\n  \.ptier\{([^}]*)\}", c).group(1)
    assert "border-radius" not in base, "the cells grew corners again"
    assert "border-left:1px solid var(--hair)" in base, "the dividers are gone"
    pro = re.search(r"\.ptier-c\{([^}]*)\}", c).group(1)
    assert "rgba(184,106,220" in pro, "Pro lost its wash"
    # shared button baseline: the base rule sets the padding once, and Pro's
    # own shorthand must end on the same bottom value
    base_bottom = re.search(r"padding:([^;}]+)", base).group(1).split()[0]
    pro_pad = re.search(r"padding:([^;}]+)", pro)
    if pro_pad:
        toks = pro_pad.group(1).split()
        bottom = toks[2] if len(toks) >= 3 else toks[0]
        assert bottom == base_bottom, f"the CTA baseline is broken: {bottom} vs {base_bottom}"
    fig = re.search(r"\n  \.ptier-fig\{([^}]*)\}", c).group(1)
    pro_fig = re.search(r"\.ptier-c \.ptier-fig\{([^}]*)\}", c).group(1)
    assert "font-size:30px" in fig and "font-size:44px" in pro_fig, \
        "the price no longer steps up the ladder"


def test_the_dead_faq_stylesheet_is_gone():
    """A complete second FAQ stylesheet for markup that does not exist. Three of
    its rules used selectors the LIVE FAQ also uses and, being later in the
    sheet, won — so dead CSS was overriding live CSS and .faq-a ignored the
    measure no matter what the real rule said."""
    c = css("landing")
    assert ".faq-list{" not in c, "the dead FAQ block is back"
    assert c.count(".faq-a{") == 1, "there are two .faq-a rules again"
    from src.dashboard.api import LANDING_HTML
    assert 'class="faq-cols"' in LANDING_HTML, "the live FAQ markup changed"


# ── phase 4: the post-login states ───────────────────────────────────────────

def _dash() -> str:
    from src.dashboard.aurora_html import DASHBOARD_HTML
    return DASHBOARD_HTML


def test_the_welcome_modal_is_gone_entirely():
    """It opened on every new account with ~250 words and five numbered steps,
    blocking the product behind a document. Deleted rather than disabled, so it
    cannot come back by uncommenting one line."""
    d = _dash()
    assert "function WelcomeOverlay" not in d, "the component is back"
    assert "Welcome to Highlightz" not in d, "its copy is back"
    assert "<WelcomeOverlay" not in d, "it is being rendered again"


def test_first_run_does_one_thing():
    """One input, one button, one line saying what happens next."""
    d = _dash()
    assert "function FirstRun(" in d
    assert "Add a channel to watch." in d
    body = d[d.index("function FirstRun("):]
    body = body[:body.index("\n}\n")]
    assert body.count("<input") == 1, "first run has more than one input"
    assert body.count("<button") == 1, "first run offers more than one action"


def test_first_run_replaces_the_shell_rather_than_sitting_inside_it():
    """A nav rail, a platform switch and a live pill are answers to questions
    somebody with no channels has not asked. The gate must return BEFORE the
    shell is built."""
    d = _dash()
    gate = d.index("return <FirstRun onAdd={addStream}/>;")
    shell = d.index("<nav className={'rd-nav'")
    assert gate < shell, "the shell is constructed before the first-run gate"


def test_first_run_is_gated_on_clips_too_not_just_streams():
    """Somebody who added a channel, collected clips and later removed the
    channel is not a new user. Dropping them onto a bare input would read as
    their account having been wiped."""
    d = _dash()
    g = d[d.index("if (me && me.id && Object.keys(streams).length === 0"):]
    g = g[:g.index("}")]
    assert "Object.keys(clips).length === 0" in g, "the gate ignores existing clips"
    assert "seenBefore" in g, "the gate ignores whether they have been here before"


def test_the_wake_plays_once_and_only_on_the_first_channel():
    """On the second and later adds it would be a 1.5s wall in front of a
    dashboard the user is already using — the thing this phase removes."""
    d = _dash()
    assert "const first = Object.keys(streams).length === 0;" in d
    assert "if (first) {" in d and "setWake({channel:s.channel" in d


def test_the_wake_animates_only_transform_and_opacity():
    d = _dash()
    for sel in (".wake-chip", ".wake-frame", ".wake-rule", ".wake-status"):
        rule = re.search(re.escape(sel) + r"\{([^}]*)\}", d)
        assert rule, sel
        trans = re.search(r"transition:([^;}]*)", rule.group(1))
        if trans:
            for prop in ("width", "height", "top", "left", "margin", "padding"):
                assert prop not in trans.group(1), f"{sel} transitions {prop}"


def test_the_wake_counter_cannot_resize_its_own_container():
    """A counter that reflows the box around it is the jitter this project
    already fixed once on the landing page."""
    rule = re.search(r"\.wake-score\{([^}]*)\}", _dash()).group(1)
    assert "tabular-nums" in rule and "min-width" in rule


def test_reduced_motion_flattens_the_wake_to_its_final_frame():
    d = _dash()
    blocks = [b.split("}\n}")[0] for b in d.split("@media(prefers-reduced-motion:reduce){")[1:]]
    wake = [b for b in blocks if "wake" in b]
    assert wake, "the wake ignores prefers-reduced-motion"
    assert "transition:none" in wake[0]
    assert "opacity:1" in wake[0] and "transform:none" in wake[0], \
        "reduced motion hides the wake instead of showing its end state"
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in DASHBOARD_HTML, \
        "the counter still animates under reduced motion"


def test_the_returning_count_leads():
    """Clip Review is the screen a returning user lands on, and the count was
    set at 14px in a row of controls — the same size as the sort labels."""
    rule = re.search(r"\.rd-toolbar-count\{([^}]*)\}", _dash()).group(1)
    assert re.search(r"font-size:var\(--t-(h1|h2|h3|display)\)", rule), \
        "the count is back to body size"
    assert "tabular-nums" in rule and "min-width" in rule, \
        "the count can shift the controls beside it when it changes"


def test_no_third_copy_of_the_queue_numbers():
    """A TodayHeader was planned for the Live Streams screen. Rendering the app
    showed the default route is Clip Review, whose toolbar already carries the
    count, the live channels and the weekly meter — so it would have been a
    third copy of the same data on a screen nobody lands on."""
    d = _dash()
    assert "function TodayHeader" not in d, "the duplicate header was shipped"
    assert "kept this week" in d, "the weekly meter went missing"


def test_the_embedded_js_carries_no_backslash_escapes():
    """This file is a Python triple-quoted string. A JS regex escaping a dot is
    an invalid Python escape — a warning today, a SyntaxError later, at which
    point the module stops importing and the app does not boot. The first
    version of FirstRun shipped one, and so did the comment warning about it."""
    import warnings, py_compile
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        py_compile.compile("src/dashboard/aurora_html.py", doraise=True,
                           cfile="/tmp/_phase4_escape_check.pyc")
    bad = [str(w.message) for w in caught if "invalid escape" in str(w.message)]
    assert not bad, f"invalid escapes: {bad}"


# ── phase 5: polish ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("page", PAGES)
def test_transitions_use_the_duration_tokens(page):
    """Eleven distinct durations on the dashboard and eight on the landing page
    before this. Anything over 500ms is an ambient loop or a deliberate slow
    reveal and keeps its own value."""
    bad = []
    for m in re.finditer(r"transition:([^;}\n]+)", css(page)):
        for seg in m.group(1).split(","):
            for lit in re.findall(r"(?<![\w.])(\d*\.?\d+)(m?s)\b", seg):
                ms = float(lit[0]) * (1 if lit[1] == "ms" else 1000)
                if ms <= 500:
                    bad.append(lit[0] + lit[1])
    assert not bad, f"{page}: raw short durations still in transitions: {sorted(set(bad))}"


@pytest.mark.parametrize("page", PAGES)
def test_one_easing_curve_plus_one_named_exception(page):
    """Four curves were written out longhand. The overshoot is kept — it is a
    deliberate character choice — but as a token, so a third variant cannot be
    added by hand."""
    c = re.sub(r"/\*.*?\*/", "", css(page), flags=re.S)   # comments are not values
    raw = re.findall(r"cubic-bezier\([^)]*\)", c)
    defs = len(re.findall(r"--ease(?:-spring)?:\s*cubic-bezier", c))
    assert len(raw) == defs, \
        f"{page}: {len(raw) - defs} curves still written out longhand"


def test_the_dangling_timing_function_is_gone():
    """Phase 1a rewrote `transition:all Xs cubic-bezier(...)` by replacing only
    the `all Xs` part, which left the original curve dangling after the new
    one. Two timing functions on one segment is invalid, so the browser drops
    the whole declaration and the element ends up with no transition at all."""
    for page in PAGES:
        assert not re.search(r"var\(--ease\)\s+cubic-bezier", css(page)), \
            f"{page}: a transition has two timing functions"


def test_every_page_honours_reduced_motion():
    """The paywall had none at all — 'we honour this everywhere except the page
    that asks you for money' is not a position worth holding."""
    from src.dashboard.api import PAYWALL_HTML
    for page in PAGES:
        assert "prefers-reduced-motion" in css(page), f"{page} ignores it"
    assert "prefers-reduced-motion" in PAYWALL_HTML, "the paywall ignores it"


def test_the_product_has_keyboard_focus_rings():
    """It had NONE — zero :focus-visible rules in the whole dashboard against
    five on the marketing pages, so tabbing through the app moved an invisible
    cursor. Verified in Chromium by pressing Tab 60 times: 0 of 58 stops
    without a ring, on all four pages."""
    d = css("dashboard")
    assert ":focus-visible" in d, "the dashboard has no focus rules again"
    assert "outline:2pxsolidvar(--acc)" in d.replace(" ", "")
    assert "forced-colors: active" in d, "no high-contrast fallback"


def test_focus_rings_are_focus_visible_not_focus():
    """:focus would leave a ring behind after a mouse click, which is the whole
    reason the default outline gets removed in the first place."""
    d = css("dashboard")
    block = d[d.index("KEYBOARD FOCUS"):]
    block = block[:block.index("*{box-sizing")]
    assert ":focus{" not in block, "a bare :focus rule is back"


def test_sora_has_a_metric_matched_fallback():
    """Isolated by loading one face at a time, Sora was the only one causing
    layout shift: 0.0416 on its own against 0.0004 for Lobster and 0.0001 for
    Plex Mono. The overrides are measured — the fallback now reports Sora's
    exact 97/29 ascent/descent and matches its advance to 0.4px at 100px."""
    c = css("landing")
    assert "'Sora Fallback'" in c, "the fallback face is gone"
    face = re.search(r"@font-face\{font-family:'Sora Fallback'[^}]*\}", c).group(0)
    for prop in ("size-adjust", "ascent-override", "descent-override", "line-gap-override"):
        assert prop in face, f"the fallback lost {prop}"
    assert "--sans:'Sora','SoraFallback'" in c.replace(" ", ""), \
        "the fallback is not in the stack, so it can never be used"


def test_the_fallback_sits_before_the_generic_stack():
    """After system-ui it would never be reached, and the overrides would do
    nothing at all."""
    c = css("landing").replace(" ", "")
    stack = re.search(r"--sans:([^;]+);", c).group(1)
    assert stack.index("'SoraFallback'") < stack.index("system-ui")
