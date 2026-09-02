"""The landing page's shape, pinned so it cannot drift back to the template.

The page read as AI-generated within two seconds, and two earlier passes at
colour, type and layout did not fix it because the tells were not in the paint.
They were structural:

  1. the product UI was DRAWN in CSS instead of shown (90 divs, zero images,
     a hand-set trigger score of 72.4 and invented channel names). That was
     first fixed by lifting the real app's DOM, and then removed outright: the
     page shows no picture of the product anywhere. The hero IS the detector
     running, and the clips it fires are real. test_landing_hero.py holds that
     shut; nothing here needs to police a screenshot that does not exist;
  2. the copy cadence: 110 em dashes, six "Not X / No Y" constructions, a
     one-line restatement under every single heading, and sentences almost
     uniformly 15-25 words;
  3. ten sections, the standard generated length;
  4. the component bill of materials: stat strip, numbered 5-step, 6-item
     feature grid, 3 pricing cards with tick lists and a "Most popular" badge,
     accordion FAQ. That exact sequence IS the template.

These tests hold each of those closed. They are deliberately about SHAPE rather
than wording, so the copy can keep being edited without them crying wolf.
"""

import re

import pytest

from src.dashboard import api

HTML = api.LANDING_HTML
CSS = re.search(r"<style>(.*?)</style>", HTML, re.S).group(1)
# Comments and scripts are not rendered copy; measuring them counts JS comments
# as prose, which is how "110 em dashes" would look fixed while nothing changed.
VISIBLE = re.sub(r"<!--.*?-->", "", re.sub(r"<script.*?</script>", "", HTML, flags=re.S),
                 flags=re.S)
VISIBLE = VISIBLE[VISIBLE.index("</style>"):]
# Every string on this page is now the marketing page's own. The lifted product
# DOM used to sit inside it and had to be excised before any copy measurement,
# because its clip titles come out of _generate_clip_title as "{channel} —
# {label}" and holding the product's UI to a brochure's cadence rules would mean
# editing the product to suit the brochure.


# ── tell 2: copy cadence ─────────────────────────────────────────────────────

def test_the_em_dashes_are_gone():
    n = VISIBLE.count("—") + VISIBLE.count("&mdash;")
    assert n <= 3, f"{n} em dashes in visible copy; the budget is 3"


def test_the_no_x_construction_appears_at_most_once():
    """It appeared six times. One instance survives, in How it works step two,
    where the transparency claim is the actual argument."""
    hits = []
    for pat in (r"[Nn]ot a [^<.]{0,45}", r"[Nn]o [a-z]+, no [a-z]+",
                r"[Nn]o black box"):
        hits += re.findall(pat, VISIBLE)
    assert len(hits) <= 1, f"the 'no X' construction is back {len(hits)} times: {hits}"


def test_almost_every_heading_stands_alone():
    """Heading -> one-line restatement -> content, on every section, is the
    structural tell. At most a couple should keep a subtitle, and only where it
    carries something the heading does not."""
    subs = re.findall(r'<p class="sec-sub"', VISIBLE)
    assert len(subs) <= 3, f"{len(subs)} section subtitles; most headings should stand alone"


def test_sentence_length_actually_varies():
    """Uniform rhythm reads as generated. Real writing has short ones."""
    import statistics
    txt = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", VISIBLE))
    lens = [len(s.split()) for s in re.split(r"(?<=[.!?])\s+", txt)
            if 2 < len(s.split()) < 60]
    # v4 is imagery first: a third of the prose the old page carried.
    assert len(lens) > 12, "not enough prose to measure"
    assert statistics.pstdev(lens) > 6, (
        f"sentence lengths are too uniform (stdev {statistics.pstdev(lens):.1f})")
    assert sum(1 for x in lens if x < 6) >= 5, "no short sentences anywhere"


# ── tell 3: length ───────────────────────────────────────────────────────────

def test_the_page_is_edited_down():
    n = HTML.count("<section")
    assert n <= 8, f"{n} sections; the page is back to the generated length"


@pytest.mark.parametrize("gone", ["Built for clippers first", "Everything in the box",
                                  "A complete clipping toolkit"])
def test_the_filler_sections_and_headings_stay_cut(gone):
    """Three parallel audience paragraphs, and promotional register in the
    headings."""
    assert gone not in HTML


# ── tell 4: the component bill of materials ──────────────────────────────────

def test_pricing_is_not_three_cards_with_tick_lists():
    sec = re.search(r'<section[^>]*id="pricing".*?</section>', HTML, re.S).group(0)
    assert "Most popular" not in sec, "the badge is back"
    assert sec.count("price-card") == 0, "the three-card grid is back"
    assert sec.count("&#10003;") == 0 and sec.count("class=\"ck\"") == 0, \
        "tick lists are back"


# ── craft rules ──────────────────────────────────────────────────────────────


def test_the_tonal_rhythm_is_dark_dark_light_light_dark_light_dark():
    """The v4 brief made the rhythm mandatory and the cuts hard: proof and
    numbers on black, catches and score on paper, watch on black, pricing on
    paper, the close on black. A section on the wrong ground breaks the
    sequence the whole page is built on."""
    secs = re.findall(r'<section class="([^"]*)" id="([^"]+)"', HTML)
    ids = [i for _, i in secs]
    assert ids == ["proof", "numbers", "catches", "score", "watch", "pricing", "start"], ids
    light = ["light" in c.split() for c, _ in secs]
    assert light == [False, False, True, True, False, True, False], light
    # And the light ground is the one token, painted flat.
    assert re.search(r"\.light\{background:var\(--paper\)", CSS)


def test_there_is_no_blurred_glow_blob():
    """A listed tell, and this one was also the entire cause of the page's
    horizontal overflow."""
    assert ".demo-wrap::before" not in CSS
    for m in re.finditer(r"radial-gradient\([^)]*\)", CSS):
        assert "inset:-" not in CSS[max(0, m.start() - 200):m.start()], \
            "a radial glow is being inset past its parent's edges again"


def test_no_section_fades_up_on_scroll():
    """The blanket fade-up on every section is itself a tell. The one
    orchestrated moment is the demo widget firing."""
    assert HTML.count('class="rise') == 0 and HTML.count(" rise\"") == 0


def test_buttons_have_real_press_states():
    for sel in (".btn-go:active", ".btn-ghost:active", ".btn-dark:active"):
        assert sel in CSS, f"{sel} has no pressed state"
    assert ":focus-visible" in CSS


# ── the quality floor ────────────────────────────────────────────────────────

def test_the_anchors_in_the_page_all_resolve():
    """Cutting sections is how anchors rot. The NO AI badge pointed at
    #formula for exactly as long as it took to notice."""
    ids = set(re.findall(r'id="([^"]+)"', HTML))
    for href in set(re.findall(r'href="#([^"]+)"', HTML)):
        assert href in ids, f'href="#{href}" points at a section that does not exist'


def test_anchor_targets_clear_the_sticky_nav():
    """The bar is sticky, so a jump to #pricing lands the heading UNDERNEATH it
    unless the anchor subtracts its height. This was briefly a flat 12px while
    the nav was removed; the bar is back, so the margin has to clear it again."""
    m = re.search(r"section\[id\][^{]*\{scroll-margin-top:([^}]+)\}", CSS)
    assert m, "anchor targets lost their scroll-margin"
    assert "--nav-h" in m.group(1), \
        "scroll-margin no longer clears the sticky nav — anchors land under it"


def test_the_navs_real_height_is_measured_not_assumed():
    """--nav-h has a CSS fallback, but the bar is not one fixed height: its
    links wrap in a band around 940px and it grows. A hardcoded number is wrong
    across a couple of hundred pixels of width, and both the anchor margin and
    slide 2's height are computed from it.

    ResizeObserver specifically, not just a resize listener: the Example clips
    link is revealed from JS once the showcase loads, and that can be what
    makes the links wrap — a width-only listener never fires for it."""
    assert "--nav-h" in HTML and "ResizeObserver" in HTML, \
        "the nav's height is assumed rather than measured"
    m = re.search(r"var nav = document\.querySelector\('\.nav'\).*?\}\)\(\);", HTML, re.S)
    assert m, "the nav-height measuring block is gone"
    assert "setProperty('--nav-h'" in m.group(0), \
        "the measured height is never written back"


def test_the_nav_has_no_lines_on_it():
    """Brought back on the owner's call, explicitly without lines: the bar wears
    the hero band's own top tone and ends in a fade rather than a rule, so it
    reads as part of the room instead of a strip laid over it.

    The old bar had three: a border under it, a second glowing hairline
    (.nav::after), and a bordered sparkline pill. None may come back."""
    m = re.search(r"\n  \.nav\{([^}]*)\}", CSS)
    assert m, ".nav rule not found"
    rule = m.group(1)
    assert "border" not in rule, f"the nav grew a border again: {rule}"
    assert ".nav::after" not in CSS, "the glowing hairline under the nav is back"
    assert ".trig{" not in CSS, "the bordered sparkline pill is back in the nav"
    assert "backdrop-filter" not in rule, \
        "the nav went back to a glass plate instead of blending"



def test_the_nav_wears_the_surface_it_arrives_on():
    """The bar is fixed over the top of the page. It wears the black family
    the cover and the proof section are painted in, so there is never an edge
    under it on the first two screens; over the light sections it is a dark
    bar by design (the brief's cuts are hard)."""
    nav = re.search(r"\n  \.nav\{([^}]*)\}", CSS).group(1)
    assert "#09070C" in nav, "the bar left the black family"
    proof = re.search(r"\n  \.proof\{([^}]*)\}", CSS).group(1)
    assert "background:#000" in proof, "the proof section is no longer on black"