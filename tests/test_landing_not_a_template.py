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
    assert len(lens) > 30, "not enough prose to measure"
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


def test_the_faq_is_a_grouped_single_column_accordion():
    """Dropdowns, by explicit request, after a pass that had opened them out.

    THE TELL WAS NEVER THE ACCORDION. It was twelve questions in an
    undifferentiated stack. So the list is allowed to be long again — depth was
    asked for directly — provided the two things that stop length reading as
    padding are in place: every question is a closed dropdown, so the whole set
    is scannable before anything is opened, and they are grouped under headers
    rather than poured into one run.

    GROUPS SCALE WITH THE LIST. Two headers over sixteen questions is the
    undifferentiated stack again wearing a hat, so the minimum rises with the
    count: roughly one group per six questions.
    """
    items = HTML.count('class="faq-item"')
    assert HTML.count("<details") >= items, "the questions are no longer dropdowns"
    groups = HTML.count('class="faq-h"')
    assert groups >= 2, "the groupings are gone"
    assert groups >= items / 6, (
        f"{items} questions under {groups} headers — the groups have not kept "
        f"up with the list and it reads as one long stack again")


def test_the_faq_is_one_column():
    """Two columns is the one arrangement that stops a set of questions being a
    list: the eye reads left-right-left-right, has to cross the gutter to find
    the next question, and the last row of a group leaves a hole. Asked for
    directly as a list, and it is also what lets the set be sixteen long."""
    m = re.search(r"\.faq-rows\{([^}]*)\}", CSS)
    assert m, ".faq-rows rule not found"
    assert "grid-template-columns:minmax(0,1fr)" in m.group(1), \
        "the FAQ is back to more than one column"
    assert not re.search(r"\.faq-rows\{[^}]*grid-template-columns:[^}]*1fr\)\s+minmax", CSS), \
        "the FAQ is back to more than one column"


def test_the_faq_dropdowns_start_closed():
    """An accordion rendered open is just a stack with extra markup, and it
    would put the page back to the length this pass cut it down from."""
    faq = re.search(r'<section[^>]*id="faq".*?</section>', HTML, re.S).group(0)
    assert "<details open" not in faq and "<details  open" not in faq


def test_how_it_works_has_no_oversized_numerals():
    sec = re.search(r'<section[^>]*id="how".*?</section>', HTML, re.S).group(0)
    assert "rail-node" not in sec, "the numbered rail is back"
    assert sec.count('class="cell"') == 3, "How it works is not three steps"
    # The step markers are small mono labels, not big circled digits.
    k = re.search(r"\.cell-k\{([^}]*)\}", CSS).group(1)
    assert "font-size:12px" in k and "border-radius" not in k
    assert "how-step" not in sec and "step-circle" not in sec


def test_the_features_are_a_spec_sheet_not_a_grid_of_icon_chips():
    """The banned shape is a run of identical cards with an icon in a tinted
    rounded square.

    This assertion has been wrong three times, each time because it measured
    a PROXY — a count of items, a run-length rule, then labelled groups. The
    redesign replaced the feature groups with two spec structures, and this
    measures those: an index of signal rows (one per real signal type), and a
    grid of hairline cells each led by a mono label and a number. Neither has
    an icon anywhere in it.
    """
    sec = re.search(r'<section[^>]*id="features".*?</section>', HTML, re.S).group(0)
    assert 'class="ic"' not in sec, "icons in tinted squares are back"
    assert "<svg" not in sec and "<img" not in sec, "the spec sheet grew pictures"
    rows = sec.count('class="sig"')
    assert rows >= 6, f"the signals index is {rows} rows — it is a feature list again"
    cells = re.findall(r'<div class="cell">(.*?)</div>\s*(?=<div class="cell">|</div>)',
                       sec, re.S)
    assert len(cells) >= 6, "the spec grid is gone"
    for i, c in enumerate(cells):
        assert 'class="cell-k"' in c, f"cell {i} has no label saying what it is"
        assert 'class="cell-n' in c, f"cell {i} has no headline value"


def test_each_chapter_opens_with_one_sentence():
    """The heading -> restatement -> content shape was the structural tell.
    What replaced it is one sentence, set large, above the structure it
    introduces — and only where a sentence carries something the label does
    not. How it works and Signals each get exactly one."""
    for sid in ("how", "features"):
        sec = re.search(r'<section[^>]*id="' + sid + r'".*?</section>', HTML, re.S).group(0)
        assert sec.count('class="say"') == 1, f"#{sid} does not open with one sentence"
    m = re.search(r"\n  \.say\{([^}]*)\}", CSS)
    assert m and "clamp(" in m.group(1), "the statement is no longer set large"
    assert "max-width" in m.group(1), "the statement can run the full page width"
    assert "grid-template-columns" not in m.group(1)


# ── craft rules ──────────────────────────────────────────────────────────────

def test_sections_are_not_all_weighted_the_same():
    """Uniformity everywhere is the visual signature of generated design — and
    that is still the rule. What changed is WHERE the variation lives.

    This used to require three different section PADDING values, which bought
    emphasis out of the boundaries between sections. That is the one budget
    that has to stay even: the boundary is what the eye meters the scroll by,
    and paying for emphasis from it produced a 112/112/96/80 sequence in which
    the two 112s read as hesitations. Every boundary is now exactly --s-9.

    The editorial judgement it was protecting is intact and still asserted
    below: the argument (#how) and the decision (#pricing) get more room than
    the reference material (#faq). It is now taken from the space between a
    section's title and its body, where varying it reads as emphasis rather
    than as an uneven scroll."""
    css = CSS.replace("\n", "").replace("  ", "")
    gaps = dict(re.findall(r"#(how|pricing|faq) \.sec-title\{margin-bottom:var\(--s-(\d+)\)", css))
    assert set(gaps) == {"how", "pricing", "faq"}, f"section weighting is gone: {gaps}"
    assert len(set(gaps.values())) >= 2, \
        f"every section is weighted identically again: {gaps}"
    assert int(gaps["how"]) > int(gaps["faq"]), \
        "the argument no longer gets more room than the reference material"
    # And the boundaries themselves must stay even.
    m = re.search(r"section\{padding-top:var\(--s-(\d+)\);padding-bottom:var\(--s-(\d+)\)\}", css)
    assert m and m.group(1) == m.group(2), "section boundaries are uneven again"


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
    for sel in (".btn-key:active", ".btn-quiet:active"):
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
    """Blending is the whole point: the hero band's top tone is #09070C, so the
    bar is that colour and there is no edge where one becomes the other. If the
    band's top tone is ever changed, this fails and both must move together."""
    m = re.search(r"\.hero\.hero-band\{[^}]*background:linear-gradient\(180deg,(#[0-9A-Fa-f]{6})",
                  CSS)
    assert m, "could not read the hero band's top tone"
    top = m.group(1)
    nav = re.search(r"\n  \.nav\{([^}]*)\}", CSS).group(1)
    assert top in nav, (
        f"the nav is not the same tone as the surface under it "
        f"(band starts {top}) — there will be a visible edge")


def test_the_faq_schema_matches_the_visible_questions():
    """The schema is derived from the markup, so a markup change must not empty
    it. Publishing a FAQPage with no questions is worse than publishing none."""
    import json
    blobs = re.findall(r'<script type="application/ld\+json">(.*?)</script>', HTML, re.S)
    faq = [json.loads(b) for b in blobs if json.loads(b).get("@type") == "FAQPage"][0]
    # Tag-agnostic on purpose. This pairing has flipped between <p> and
    # <summary> twice; both times a tag-specific pattern here made the test
    # compare the schema against an empty list and pass for the wrong reason,
    # which is precisely the drift it exists to catch.
    shown = re.findall(
        r'<(?:p|summary)[^>]*\bclass="[^"]*\bfaq-q\b[^"]*"[^>]*>(.*?)</(?:p|summary)>',
        HTML, re.S)
    assert len(shown) >= 5, "the visible FAQ could not be read at all"
    assert len(faq["mainEntity"]) == len(shown)


def test_the_cell_titles_are_not_the_same_colour_as_their_own_body_text():
    """WHY THE OLD FEATURE SECTION LOOKED FLAT. Its titles set no colour at
    all, so they INHERITED the body value: heading and paragraph in one ink,
    separated by two pixels of size and one weight. The spec cells keep the
    fix in its structural form — the title STATES its colour, and it is not
    the paragraph's — without the gold the old titles borrowed, because under
    the hero the orange belongs to numbers and the one button only."""
    m = re.search(r"\.cell-h\{([^}]*)\}", CSS)
    assert m, ".cell-h has no rule of its own"
    head = m.group(1)
    assert "color:" in head, \
        "the cell titles inherit their colour — one parent change and they " \
        "are the same ink as their own body text again"
    body = re.search(r"\.cell p\{([^}]*)\}", CSS).group(1)
    head_col = re.search(r"color:([^;]+)", head).group(1).strip()
    body_col = re.search(r"color:([^;]+)", body).group(1).strip()
    assert head_col != body_col, f"cell titles and body text are both {head_col}"
    assert "ember" not in head_col, "the titles took the number colour"


def test_the_cell_titles_stay_smaller_than_the_chapter_sentence():
    """The one-sentence opener carries the argument. The cell titles must not
    grow into a second set of headlines competing with it."""
    item = re.search(r"\.cell-h\{([^}]*)\}", CSS).group(1)
    lead = re.search(r"\n  \.say\{([^}]*)\}", CSS).group(1)
    size = float(re.search(r"font-size:([\d.]+)px", item).group(1))
    cap = float(re.search(r"font-size:clamp\([^,]+,[^,]+,\s*([\d.]+)px\)", lead).group(1))
    assert size < cap, (
        f"cell titles are {size}px and the statement caps at {cap}px "
        f"— the statement is no longer the largest thing in the chapter")


def test_the_headline_number_never_wraps_its_row():
    """The big value in a cell is one line or it is nothing: a "1 / 3 / 10"
    that folds onto two lines drops that cell's title and paragraph below its
    neighbours' and leaves the row ragged. The rule truncates rather than
    wraps, and only lets go of that on a phone where the cells are stacked
    and there is no row to keep."""
    n = re.search(r"\n  \.cell-n\{([^}]*)\}", CSS).group(1)
    assert "white-space:nowrap" in n and "text-overflow:ellipsis" in n, \
        "the headline number can wrap and break the row"
    assert re.search(r"@media\(max-width:600px\)\{[^@]*\.cell-n\{white-space:normal\}", CSS), \
        "the number is still forced onto one line on a phone"
