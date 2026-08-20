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
# The captured product surfaces are excluded from every COPY measurement below.
# Their strings are the app's, not the marketing page's: clip titles come out of
# _generate_clip_title as "{channel} — {label}", em dash included, and holding
# the product's own UI to a landing page's cadence rules would mean editing the
# product to suit the brochure. Structure tests still look at the whole page.
def _strip_captures(html: str) -> str:
    """Remove each .pcap subtree, matching braces properly.

    A non-greedy regex stops at the FIRST </div>, not the matching one, and
    left most of the product DOM in place — which is how this measured 23 em
    dashes and looked like the copy rewrite had failed.
    """
    out, i = [], 0
    while True:
        j = html.find('<div class="pcap"', i)
        if j == -1:
            out.append(html[i:])
            return "".join(out)
        out.append(html[i:j])
        depth, k = 0, j
        while k < len(html):
            if html.startswith("<div", k):
                depth += 1
            elif html.startswith("</div>", k):
                depth -= 1
                if depth == 0:
                    k += 6
                    break
            k += 1
        i = k


VISIBLE = _strip_captures(VISIBLE)


# ── tell 1: the product is shown, not drawn ──────────────────────────────────

def test_the_product_section_is_the_real_apps_markup():
    """Not "looks like the app" — IS the app's markup.

    The section holds DOM that the real dashboard components rendered, lifted
    by scripts/capture_product_ui.mjs. The check is that the app's own class
    names are present: nobody hand-writing an approximation reproduces rd-stream,
    rd-sigbar and rd-scorebadge, so their presence is the evidence, and their
    absence means someone drew a picture again.
    """
    sec = re.search(r'<section[^>]*id="product".*?</section>', HTML, re.S).group(0)
    for cls in ("rd-stream", "rd-clip", "rd-sigbar", "rd-scorebadge", "rd-modal"):
        assert cls in sec, f"{cls} is missing — this is not the app's own markup"
    assert sec.count('class="pcap"') == 3, "the three captured surfaces are not all there"


def test_the_captures_cannot_reach_the_network():
    """A landing page that makes no external requests must not start making
    them because a capture carried a thumbnail or an embed."""
    from src.dashboard import landing_product as P
    for name, blob in (("streams", P.STREAMS_HTML), ("review", P.REVIEW_HTML),
                       ("detail", P.DETAIL_HTML), ("css", P.PRODUCT_CSS)):
        for bad in ("http://", "https://", "<iframe", "<video", "@import"):
            assert bad not in blob, f"{name} capture contains {bad!r}"
        # <img> is allowed ONLY when it is self-contained. The thumbnails are
        # generated SVG data URIs, which make no request; anything with a real
        # src is stripped by the capture script. Banning the tag outright was
        # too blunt and would have forced the flat-colour fallback back in.
        for tag in re.findall(r"<img\b[^>]*>", blob):
            assert 'src="data:' in tag, f"{name} has a fetching image: {tag[:90]}"


def test_the_captures_cannot_take_focus():
    """They are pictures. A visitor tabbing to the pricing must not walk
    through thirty dead controls first."""
    for blob in (HTML,):
        sec = re.search(r'<section[^>]*id="product".*?</section>', blob, re.S).group(0)
        assert sec.count("<button") == 0, "a capture still has real buttons in it"
        assert sec.count("<a ") == 0, "a capture still has real links in it"
        assert sec.count("inert") == 3, "every capture must be inert"


def test_the_product_stylesheet_cannot_escape_its_wrapper():
    """The dashboard and the landing page share twelve short class names
    (accent, hot, ic, on, k, v). Every product rule is prefixed so none of them
    can repaint the marketing page."""
    from src.dashboard.landing_product import PRODUCT_CSS
    bad = []
    for line in PRODUCT_CSS.split("\n"):
        line = line.strip()
        if not line or "{" not in line:
            continue
        sel = line[:line.index("{")].strip()
        if not sel or sel.startswith("@") or sel.startswith("}"):
            continue
        # keyframe steps are not selectors
        if re.fullmatch(r"(from|to|[\d.]+%)(\s*,\s*(from|to|[\d.]+%))*", sel):
            continue
        if not all(part.strip().startswith(".pcap") for part in sel.split(",")):
            bad.append(sel[:70])
    assert bad == [], f"these product rules are not scoped to .pcap: {bad[:4]}"



def test_nothing_in_the_capture_is_scaled():
    """THE ALIGNMENT BUG, pinned.

    The captures used to lay out at their natural width and shrink with
    transform:scale() to a hardcoded pixel width. That is what threw them off
    centre: 1180px of content inside a 1720px container at 1920, pinned left,
    468px of dead space on the right. A fixed width in a fluid container cannot
    centre, and centring it would only have moved the emptiness around.

    They are live DOM, so each is handed its column and lays itself out in it,
    filling the width at every breakpoint. If a scale ever comes back, so does
    the misalignment.
    """
    css = CSS.replace(" ", "").replace("\n", "")
    assert "transform:scale(var(--sc))" not in css, "the capture is being scaled again"
    assert "width:calc(var(--cw)*1px)" not in css, \
        "the crop window is pinned to a fixed pixel width again"
    assert ".pshot.pcap{position:relative;width:100%" in css, \
        "the capture no longer fills its column"


def test_the_capture_rows_cannot_overflow_or_hug_one_side():
    """Both halves of the fix. The columns are fractional so the row always
    fills its container, and every track can shrink so nothing can push the
    page sideways."""
    css = CSS.replace(" ", "").replace("\n", "")
    assert ".pshow{display:grid;grid-template-columns:minmax(230px,.3fr)minmax(0,.7fr)" in css, \
        "the paired row's tracks are not fractional"
    assert ".pshow-alt{grid-template-columns:minmax(0,.7fr)minmax(230px,.3fr)}" in css, \
        "the alternating row does not mirror"
    assert "#product{overflow-x:clip" in css, \
        "the section does not clip, so a bleeding capture scrolls the page"


def test_the_crop_height_is_fixed_so_nothing_shifts():
    """The capture paints after layout. Without a height set in CSS the row
    would grow when it lands and push everything below it down."""
    css = CSS.replace(" ", "").replace("\n", "")
    assert "height:calc(var(--h)*1px)" in css, "the crop has no fixed height"
    assert "height:calc(var(--hm)*1px)" in css, "no fixed height on phones"
    assert re.search(r'--h:\d+;--hm:\d+', HTML), "a capture ships without its dimensions"


def test_each_capture_gets_one_callout_aimed_at_something():
    """The bland part was that nothing said where to look. One annotation per
    picture, two or three words, positioned in percentages because the capture
    reflows with its column."""
    calls = re.findall(r'<span class="pcall ([^"]+)" style="left:([\d.]+)%;top:([\d.]+)%"', HTML)
    assert len(calls) == 2, f"expected one callout per capture, found {len(calls)}"
    for cls, x, y in calls:
        assert cls.startswith("pcall-to-"), f"callout {cls} has no direction"
        assert 0 < float(x) < 100 and 0 < float(y) < 100, "a callout points off the picture"
    words = re.findall(r'<span class="pcall[^>]*><i></i><b>([^<]+)</b>', HTML)
    for w in words:
        assert 1 <= len(w.split()) <= 3, f"callout label is not 2-3 words: {w!r}"


def test_the_callouts_are_hidden_where_they_would_aim_at_nothing():
    """Below the tablet breakpoint the product's own responsive layout moves
    the thing being pointed at, so a callout would point into empty space."""
    css = CSS.replace(" ", "").replace("\n", "")
    assert ".pcall{display:none}" in css, \
        "the callouts survive into the phone layout, where their targets have moved"


def test_the_copy_sits_beside_the_picture_not_above_it():
    """The empty band each screenshot floated in was the stacked layout. Paired
    columns are what removed it."""
    assert HTML.count('class="pshow-copy"') == 2
    assert HTML.count('class="pshow-media') == 2
    assert "pshow-alt" in HTML, "the rows do not alternate sides"


def test_one_capture_is_layered_over_another():
    """The clip detail overlaps the monitor's corner rather than taking a row
    of its own, so the section reads as one surface with a panel on it."""
    css = CSS.replace(" ", "").replace("\n", "")
    assert 'class="pshot-inset"' in HTML
    assert ".pshot-inset{position:absolute" in css and "z-index:2" in css


# ── thumbnails ───────────────────────────────────────────────────────────────
# REPORTED: "flat brown rectangles". Diagnosis: no image was ever set — the
# capture seeded thumbnail_url as "" — so the components took their fallback
# branch, thumbFor(clip.channel). That hashes the CHANNEL, so every clip from
# one channel got a byte-identical rectangle, and a queue dominated by jynxzi
# (hue 24) and lacy (hue 35) rendered as brown repeats. Not a 404, and not one
# shared colour bleeding through.

def test_the_seeded_account_looks_used_not_demoed():
    """Placeholder-perfect data is its own tell. A real account mid-session has
    uneven numbers, a channel that is not live, and a rejected clip sitting in
    the queue."""
    from src.dashboard import landing_product as P
    all_html = P.STREAMS_HTML + P.REVIEW_HTML + P.DETAIL_HTML
    assert "61.7" in all_html, "the live score is a round number"
    assert "offline" in P.STREAMS_HTML, "every channel is live, which never happens"
    assert "rejected" in P.REVIEW_HTML, "nothing in the queue was ever rejected"
    # Real states from the real enums, not invented ones.
    for state in ("pending", "approved", "rejected"):
        assert state in P.REVIEW_HTML, f"the queue shows no {state} clip"


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


def test_the_faq_is_a_short_grouped_accordion():
    """Dropdowns, by explicit request, after a pass that had opened them out.

    The accordion itself was never the real tell; twelve questions in a stack
    was. So the disclosure widgets are back and the reductions stay: seven
    questions rather than twelve, split under two headers, with the rest moved
    to the walkthrough. Short enough to read the whole list before opening one.
    """
    assert HTML.count('class="faq-item"') == 7, "the FAQ grew back past seven"
    assert HTML.count("<details") >= 7, "the questions are no longer dropdowns"
    assert HTML.count('class="faq-h"') == 2, "the two groupings are gone"


def test_the_faq_dropdowns_start_closed():
    """An accordion rendered open is just a stack with extra markup, and it
    would put the page back to the length this pass cut it down from."""
    faq = re.search(r'<section[^>]*id="faq".*?</section>', HTML, re.S).group(0)
    assert "<details open" not in faq and "<details  open" not in faq


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
    # Tag-agnostic on purpose. This pairing has flipped between <p> and
    # <summary> twice; both times a tag-specific pattern here made the test
    # compare the schema against an empty list and pass for the wrong reason,
    # which is precisely the drift it exists to catch.
    shown = re.findall(
        r'<(?:p|summary)[^>]*\bclass="[^"]*\bfaq-q\b[^"]*"[^>]*>(.*?)</(?:p|summary)>',
        HTML, re.S)
    assert len(shown) >= 5, "the visible FAQ could not be read at all"
    assert len(faq["mainEntity"]) == len(shown)


def _clip_imgs():
    from src.dashboard import landing_product as P
    return re.findall(r"<img[^>]*>", P.REVIEW_HTML)


def test_every_clip_has_a_thumbnail():
    assert len(_clip_imgs()) == 8, "the queue is back to fallback rectangles"


def test_no_thumbnail_reaches_the_network():
    """Generated rather than fetched, so the page keeps making zero external
    requests. Real Helix thumbnails would be the better asset but none are
    reachable from a dev container, which has no clips.json."""
    for i in _clip_imgs():
        assert 'src="data:image/svg+xml,' in i, f"a thumbnail is not self-contained: {i[:90]}"
    from src.dashboard import landing_product as P
    for blob in (P.STREAMS_HTML, P.REVIEW_HTML, P.DETAIL_HTML):
        assert "http://" not in blob and "https://" not in blob


def test_a_row_of_eight_reads_as_eight_different_clips():
    """The actual requirement. Hues step by the golden angle so they separate
    across the wheel instead of clustering in one band."""
    hues = []
    for i in _clip_imgs():
        m = re.search(r"fill%3D%22hsl\((\d+)%20", i) or re.search(r'fill="hsl\((\d+) ', i)
        assert m, "could not read a thumbnail's base hue"
        hues.append(int(m.group(1)))
    assert len(set(hues)) == 8, f"only {len(set(hues))} distinct hues across 8 clips: {hues}"
    # And spread, not merely unequal: no two within 20 degrees of each other.
    ordered = sorted(hues)
    gaps = [b - a for a, b in zip(ordered, ordered[1:])]
    assert min(gaps) >= 20, f"two thumbnails sit {min(gaps)} degrees apart: {ordered}"


def test_every_thumbnail_has_its_own_fallback_colour():
    """The components' onError hands back to thumbFor(channel) — the shared
    per-channel gradient that caused this in the first place. Each image
    carries its own base hue as a background so a failure to paint still shows
    something per-clip."""
    bgs = [re.search(r"background:hsl\((\d+)", i) for i in _clip_imgs()]
    assert all(bgs), "a thumbnail has no per-item fallback colour"
    vals = [b.group(1) for b in bgs]
    assert len(set(vals)) == len(vals), f"two clips share a fallback colour: {vals}"


def test_thumbnails_cannot_shift_the_layout_or_stretch():
    """16:9 with cover, and the box is sized by the container's aspect-ratio so
    nothing moves when an image decodes."""
    from src.dashboard.landing_product import PRODUCT_CSS
    css = PRODUCT_CSS.replace(" ", "")
    assert ".rd-media{position:relative;width:100%;aspect-ratio:16/9" in css, \
        "the thumbnail box lost its fixed aspect ratio"
    for i in _clip_imgs():
        assert "object-fit: cover" in i or "objectFit" in i, "a thumbnail can letterbox or stretch"


def test_no_flat_single_colour_thumbnail_survives():
    """The whole point: not one solid fill anywhere behind a clip."""
    from src.dashboard import landing_product as P
    for name, blob in (("streams", P.STREAMS_HTML), ("review", P.REVIEW_HTML),
                       ("detail", P.DETAIL_HTML)):
        flat = re.findall(r'class="(?:rd-)?thumb"[^>]*style="background:\s*(?:#|rgb)', blob)
        assert not flat, f"{name} still paints a flat colour behind a clip"
