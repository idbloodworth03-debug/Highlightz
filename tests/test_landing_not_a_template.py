"""The landing page's shape, pinned so it cannot drift back to the template.

The page read as AI-generated within two seconds, and two earlier passes at
colour, type and layout did not fix it because the tells were not in the paint.
They were structural:

  1. the product UI was DRAWN in CSS instead of shown (90 divs, zero images,
     a hand-set trigger score of 72.4 and invented channel names);
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


# ── tell 1: the product is shown, not drawn ──────────────────────────────────

def test_the_product_section_uses_real_captures_not_css():
    """It is slots for real files. If someone rebuilds the dashboard in divs
    again, the div count is what gives it away."""
    sec = re.search(r'<section[^>]*id="product".*?</section>', HTML, re.S).group(0)
    assert sec.count("<div") < 12, (
        "the product section is being drawn in HTML again "
        f"({sec.count('<div')} divs) instead of showing a capture")
    assert "pshot" in sec, "the capture slots are gone"


def test_every_capture_slot_reserves_its_space():
    """No CLS in either direction: the box holds the capture's aspect ratio
    whether the file has landed or not."""
    for sh in api._LANDING_SHOTS:
        assert sh.w > 0 and sh.h > 0
    assert "--pw:" in HTML and "--ph:" in HTML
    assert "aspect-ratio:var(--pw)/var(--ph)" in CSS.replace(" ", "")


def test_a_missing_capture_degrades_instead_of_breaking():
    """A broken-image icon reads as a bug and makes the page look unfinished."""
    assert "not captured yet" in HTML
    assert "pshot-ph" in CSS


def test_the_invented_demo_data_is_gone():
    """72.4, novafps in the mockups, moonvale, drift_season. Placeholder-perfect
    demo data is its own tell, and these were the page's."""
    for fake in ("72.4", "moonvale", "drift_season", "Perfect comedic timing"):
        assert fake not in HTML, f"invented demo data is back: {fake}"


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


def test_the_faq_is_not_an_accordion():
    assert "<details" not in HTML, "the FAQ went back to being an accordion"
    assert HTML.count('class="faq-item"') <= 8, "the FAQ grew back"
    assert "faq-cols" in CSS, "the two-column Q&A layout is gone"


def test_how_it_works_has_no_oversized_numerals():
    sec = re.search(r'<section[^>]*id="how".*?</section>', HTML, re.S).group(0)
    assert "rail-node" not in sec, "the numbered rail is back"
    assert sec.count("flow-step") == 3, "How it works is not three steps"


def test_the_feature_grid_is_not_a_symmetric_grid_of_icon_chips():
    sec = re.search(r'<section[^>]*id="features".*?</section>', HTML, re.S).group(0)
    assert 'class="ic"' not in sec, "icons in tinted squares are back"
    assert "feat-wide" in sec, "the asymmetric layout is gone"
    # Counted with a word boundary: a bare count of 'class="feat' also matches
    # the feat-grid container and reports five.
    items = re.findall(r'class="feat(?:\s+[^"]*)?"', sec)
    assert len(items) == 4, f"the feature list is {len(items)} items, not four"


# ── craft rules ──────────────────────────────────────────────────────────────

def test_section_padding_is_not_one_value_everywhere():
    """Uniformity everywhere is the visual signature of generated design."""
    vals = set(re.findall(r"#(?:how|pricing|product|features|examples|faq)\{padding-top:(\d+)px",
                          CSS.replace("\n", "").replace("  ", "")))
    assert len(vals) >= 3, f"section padding takes only {len(vals)} value(s)"


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


def test_the_faq_schema_matches_the_visible_questions():
    """The schema is derived from the markup, so a markup change must not empty
    it. Publishing a FAQPage with no questions is worse than publishing none."""
    import json
    blobs = re.findall(r'<script type="application/ld\+json">(.*?)</script>', HTML, re.S)
    faq = [json.loads(b) for b in blobs if json.loads(b).get("@type") == "FAQPage"][0]
    shown = re.findall(r'<p class="faq-q">(.*?)</p>', HTML, re.S)
    assert len(faq["mainEntity"]) == len(shown) >= 5
