"""Head tags, sitemap and entity schema on the public pages.

WHAT THIS EXISTS TO CATCH. Every finding here came out of reading the real
rendered output of all seven public pages, not from a checklist:

  - four pages declared no canonical at all, so `/tos`, `/tos/`, the `www.`
    form and the `http://` form were four competing documents;
  - `/opt-out` had no meta description, leaving Google to invent the snippet
    on the page an annoyed broadcaster lands on;
  - the sitemap was bare `<loc>` elements with no `lastmod`, so a crawler had
    no signal about what had changed since its last visit;
  - `/tutorial` and `/compare` still pointed at og-card-v2 after the landing
    page moved to v4.

None of these break a page, which is exactly why none of them were noticed.
They only show up in output nobody renders.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

from src.dashboard.api import app

PUBLIC = ["/", "/tutorial", "/compare", "/tos", "/privacy", "/cookies", "/opt-out"]

client = TestClient(app)


def head(path: str) -> str:
    r = client.get(path)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    m = re.search(r"<head>(.*?)</head>", r.text, re.S)
    assert m, f"{path} has no <head>"
    return m.group(1)


# ── canonical ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", PUBLIC)
def test_every_public_page_declares_its_canonical_url(path):
    m = re.search(r'<link rel="canonical" href="([^"]+)">', head(path))
    assert m, f"{path} has no canonical link"
    assert m.group(1) == "https://highlightz.app" + path, \
        f"{path} points its canonical somewhere else: {m.group(1)}"


@pytest.mark.parametrize("path", PUBLIC)
def test_exactly_one_canonical(path):
    """Two canonicals is the same as none — a crawler discards both."""
    assert head(path).count('rel="canonical"') == 1


# ── description and link previews ────────────────────────────────────────────

@pytest.mark.parametrize("path", PUBLIC)
def test_every_public_page_has_a_usable_meta_description(path):
    m = re.search(r'<meta name="description" content="([^"]*)">', head(path))
    assert m, f"{path} has no meta description"
    # Under ~70 chars Google tends to write its own; over ~160 it truncates.
    assert 70 <= len(m.group(1)) <= 320, \
        f"{path} description is {len(m.group(1))} chars"


@pytest.mark.parametrize("path", PUBLIC)
def test_every_public_page_previews_as_a_card(path):
    h = head(path)
    for tag in ('property="og:title"', 'property="og:description"',
                'property="og:url"', 'property="og:image"',
                'name="twitter:card" content="summary_large_image"'):
        assert tag in h, f"{path} is missing {tag}"


@pytest.mark.parametrize("path", PUBLIC)
def test_one_card_image_across_the_whole_site(path):
    """The drift this catches already happened once: the landing page moved to
    v4 and the two content pages stayed on v2."""
    imgs = set(re.findall(r'/static/(og-card[^"]*\.png)', head(path)))
    assert imgs == {"og-card-v4.png"}, f"{path} uses {imgs}"


def test_the_retired_card_files_still_ship():
    """Not dead weight. Posts shared before each rename still reference the old
    filename, and a scraper re-fetching one gets a broken image if it is gone."""
    from src.dashboard.api import _STATIC_DIR
    from pathlib import Path
    for old in ("og-card.png", "og-card-v2.png", "og-card-v3.png"):
        assert (Path(_STATIC_DIR) / old).exists(), old


# ── the opt-out flow must not be indexed ─────────────────────────────────────

def test_the_opt_out_flow_states_are_noindex():
    """`/opt-out/confirm` and `/opt-out/success` are mid-flow screens, nearly
    identical to each other and to the page they follow. Letting them into the
    index is how a site earns a thin-duplicate problem."""
    from src.dashboard.api import _OPTOUT_CONFIRM_HTML, _OPTOUT_SUCCESS_HTML
    for name, html in (("confirm", _OPTOUT_CONFIRM_HTML),
                       ("success", _OPTOUT_SUCCESS_HTML)):
        assert '<meta name="robots" content="noindex">' in html, name


def test_the_opt_out_landing_itself_is_still_indexable():
    """It is the page a broadcaster is supposed to FIND. noindex here would be
    the opposite of the compliance posture the whole feature exists for."""
    assert "noindex" not in head("/opt-out")


# ── sitemap ──────────────────────────────────────────────────────────────────

def test_sitemap_lists_every_public_page_with_a_lastmod():
    xml = client.get("/sitemap.xml").text
    for path in PUBLIC:
        assert f"<loc>https://highlightz.app{path}</loc>" in xml, path
    entries = re.findall(r"<url><loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?</url>", xml)
    assert len(entries) == len(PUBLIC)
    for loc, mod in entries:
        assert mod, f"{loc} has no lastmod"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", mod), f"{loc}: {mod!r} is not a W3C date"


def test_lastmod_is_the_real_mtime_of_the_file_that_renders_the_page():
    """A sitemap that says everything changed today, every day, is a sitemap
    crawlers learn to ignore. So this compares against the actual mtime rather
    than merely checking a date came back — `time.gmtime()` with no argument
    also returns a valid-looking date, and it is exactly the wrong one."""
    import time
    from pathlib import Path
    from src.dashboard.api import _page_lastmod, _PAGE_SOURCE
    assert set(_PAGE_SOURCE) == set(PUBLIC)
    root = Path(__file__).resolve().parents[1]
    for path, src in _PAGE_SOURCE.items():
        expected = time.strftime("%Y-%m-%d", time.gmtime((root / src).stat().st_mtime))
        assert _page_lastmod(path) == expected, \
            f"{path}: reported {_page_lastmod(path)}, file says {expected}"


def test_a_page_whose_source_cannot_be_read_is_omitted_not_faked():
    """The failure mode worth designing against is not a crash, it is a
    plausible lie. If the file is unreadable the honest answer is no lastmod at
    all; stamping it with today teaches crawlers the field is noise."""
    from src.dashboard import api as api_mod
    assert api_mod._page_lastmod("/no-such-page") == ""
    saved = api_mod._PAGE_SOURCE
    try:
        api_mod._PAGE_SOURCE = dict(saved, **{"/tos": "src/dashboard/does_not_exist.py"})
        assert api_mod._page_lastmod("/tos") == ""
    finally:
        api_mod._PAGE_SOURCE = saved


def test_the_sitemap_only_lists_pages_robots_allows():
    """Advertising a URL in the sitemap that robots.txt forbids is a direct
    contradiction, and Search Console reports it as an error."""
    robots = client.get("/robots.txt").text
    blocked = [l.split(":", 1)[1].strip()
               for l in robots.splitlines() if l.startswith("Disallow:")]
    xml = client.get("/sitemap.xml").text
    for loc in re.findall(r"<loc>https://highlightz\.app([^<]*)</loc>", xml):
        for b in blocked:
            assert not (loc.startswith(b) and b != "/"), f"{loc} is disallowed by {b}"


# ── the llms.txt pointer ─────────────────────────────────────────────────────

@pytest.mark.parametrize("path", PUBLIC)
def test_every_public_page_points_at_the_llm_brief(path):
    assert ('<link rel="alternate" type="text/markdown" '
            'href="https://highlightz.app/llms.txt"') in head(path), path


def test_robots_names_the_llm_brief_too():
    assert "https://highlightz.app/llms.txt" in client.get("/robots.txt").text


def test_the_brief_it_points_at_is_actually_reachable():
    """A rel=alternate to a URL that redirects to the login page advertises
    nothing. This exact mistake was made when the route was first added."""
    r = client.get("/llms.txt", follow_redirects=False)
    assert r.status_code == 200, f"/llms.txt -> {r.status_code}"
    assert r.headers["content-type"].startswith("text/plain")


# ── the organisation as an entity ────────────────────────────────────────────

def _graph() -> list[dict]:
    nodes = []
    for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                        client.get("/").text, re.S):
        d = json.loads(b)
        nodes += d.get("@graph", [d])
    return nodes


def test_the_landing_page_declares_a_top_level_organization():
    """Nested inside SoftwareApplication as `publisher` it describes who made
    the product. Standalone, with an @id, it is an entity a knowledge graph can
    attach to — which is also what an LLM reads to answer who runs the site."""
    org = [n for n in _graph() if n.get("@type") == "Organization"
           and "@id" in n]
    assert len(org) == 1, "expected exactly one top-level Organization node"
    org = org[0]
    assert org["name"] == "ANTI Technology LLC"
    assert org["address"]["addressRegion"] == "NJ", \
        "the LLC is registered in New Jersey"


def test_the_website_node_points_back_at_the_organization():
    """The @id cross-reference is the whole reason these share one @graph. A
    dangling publisher reference makes them two unrelated entities."""
    nodes = _graph()
    site = [n for n in nodes if n.get("@type") == "WebSite"][0]
    org = [n for n in nodes if n.get("@type") == "Organization" and "@id" in n][0]
    assert site["publisher"]["@id"] == org["@id"]


def test_same_as_is_absent_rather_than_guessed():
    """sameAs asserts 'this profile IS us'. With no confirmed handles in the
    codebase, emitting any would be a claim we cannot back — and a wrong one is
    worse than an empty list. When ORG_PROFILES is filled in, it appears."""
    from src.dashboard.api import ORG_PROFILES, _org_schema
    org = [n for n in _graph() if n.get("@type") == "Organization" and "@id" in n][0]
    if ORG_PROFILES:
        assert set(org["sameAs"]) == set(ORG_PROFILES)
    else:
        assert "sameAs" not in org
    # And the wiring works, so filling the tuple in is genuinely all it takes.
    import src.dashboard.api as api_mod
    saved = api_mod.ORG_PROFILES
    try:
        api_mod.ORG_PROFILES = ("https://x.com/example",)
        assert '"sameAs": ["https://x.com/example"]' in _org_schema()
    finally:
        api_mod.ORG_PROFILES = saved


def test_all_landing_json_ld_parses():
    """One malformed block invalidates the lot as far as a crawler cares."""
    assert len(_graph()) >= 3


# ── the page a crawler reads, not the page a browser draws ───────────────────

def test_the_legal_pages_wear_the_site_s_bar_and_footer():
    """Owner (2026-09-02): the legal pages were the last three in the pre-v4
    purple, with a Back link that went to /login. They share one stylesheet,
    the landing page's bar and its one-row footer now, and the logo goes home."""
    from src.dashboard.api import COOKIES_HTML, PRIVACY_HTML, TOS_HTML, _LEGAL_STYLE
    for name, html in (("tos", TOS_HTML), ("privacy", PRIVACY_HTML), ("cookies", COOKIES_HTML)):
        assert '<nav class="nav">' in html and '<a href="/" class="nav-logo">' in html, name
        assert '<span class="fl">&copy; 2026 ANTI Technology LLC</span>' in html, name
        assert '<main class="legal">' in html, name
        assert 'class="back"' not in html and 'href="/login" class="back"' not in html, name
        assert "#0e0b11" not in html.split("</head>")[0].lower() or _LEGAL_STYLE in html, name
        assert "Lobster" not in html and "Inter,system-ui" not in html, name
    assert "--paper:#F4F4F2" in _LEGAL_STYLE and "--ember:#F7A745" in _LEGAL_STYLE


def test_the_llm_brief_does_not_describe_the_highlight_mechanism():
    """The owner keeps how Highlight clips are found private. The brief used
    to say "unusual spikes in audience clipping activity" — the plainest
    statement of the mechanism anywhere on the site, on the one file written
    for machines to quote."""
    from fastapi.testclient import TestClient
    from src.dashboard.api import app
    body = TestClient(app).get("/llms.txt").text.lower()
    assert "highlight clip" in body, "the brief no longer explains Highlight clips at all"
    for tell in ("audience clipping", "viewers clip", "spike", "crowd"):
        assert tell not in body, f"llms.txt gives the mechanism away: {tell!r}"


def test_the_full_brief_is_public_and_carries_the_faq_and_the_comparison():
    """/llms-full.txt (llmstxt.org): the whole of the public copy as markdown,
    generated from the same content modules the pages render, so a model that
    fetches one file gets the FAQ answers, the walkthrough, the plans and the
    comparison rows without parsing HTML."""
    from fastapi.testclient import TestClient
    from src.dashboard import api, compare_content as CC, tutorial_content as TC
    from src.billing.plans import PLAN_LIMITS
    r = TestClient(api.app).get("/llms-full.txt", follow_redirects=False)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    body = r.text
    assert body.lstrip().startswith("# Highlightz")
    assert "What are Highlight clips?" in body and "green label" in body
    for q in ("Is this allowed on Twitch?", "Do you record or store my stream?", "How does billing work?"):
        assert q in body, f"landing FAQ question missing from the full brief: {q}"
    for sec in TC.FEATURES:
        assert sec.title in body, f"walkthrough section missing: {sec.title}"
    for feat, *_ in CC.FEATURES:
        assert feat in body, f"comparison row missing: {feat}"
    for label, *_ in CC.CREDITS["rows"]:
        assert label in body, f"credit row missing: {label}"
    assert f"${PLAN_LIMITS['pro']['price']}" in body and str(PLAN_LIMITS["free"]["max_pending"]) in body
    assert "/llms-full.txt" in TestClient(api.app).get("/llms.txt").text
    assert "/llms-full.txt" in api._OPEN_PATHS
    low = body.lower()
    for tell in ("audience clipping", "viewers clip", "spike in audience", "crowd"):
        assert tell not in low, f"llms-full.txt gives the mechanism away: {tell!r}"


def test_the_core_claims_survive_with_no_javascript_and_no_css():
    """THE REGRESSION THIS GUARDS. One session removed the hero's copy, then
    the nav, then rebuilt everything below the cover — each step deleted
    visible text, and the only reason the page still reads is that the claims
    happen to survive elsewhere. An LLM or search crawler gets exactly this:
    the body with scripts, styles and comments stripped. Whatever the design
    does next, these facts must stay in that text.

    Extraction detail that already bit once: index("<body") matches the LITERAL
    <body> inside a CSS comment thousands of characters before the real tag —
    the body must be found after </head>."""
    import re
    from src.dashboard.api import LANDING_HTML as H
    start = H.index("<body", H.index("</head>"))
    body = re.sub(r"<script.*?</script>", " ", H[start:], flags=re.S)
    body = re.sub(r"<!--.*?-->", " ", body, flags=re.S)
    txt = re.sub(r"<[^>]+>", " ", body)
    txt = re.sub(r"\s+", " ", txt).lower()

    for claim in (
        "highlightz",                      # who
        "twitch",                          # where
        "clip",                            # what
        "formula",                         # how, and the differentiator
        "no black box",                    # the position
        "free",                            # the offer
        "official twitch api",             # the compliance story
        "opt out",                         # the streamer-consent story
        "review queue",                    # nothing auto-publishes
        "vod scanner",                     # the pro feature
        "threshold",                       # the mechanism's vocabulary
    ):
        assert claim in txt, f"{claim!r} is no longer in the crawlable text"
    # and enough of it to summarise from — a page of chrome with 500 chars of
    # prose is not a source, whatever the probes say
    # 2000, down from 4000 (landing v4, 2026-09-02). The brief made the page
    # imagery first with three or four sentences a section; the claims above
    # are what a crawler needs and every one is still in the text. The floor
    # now guards against the text collapsing to nav and footer, not against
    # a page that says less on purpose.
    assert len(txt) > 2000, f"only {len(txt)} chars of crawlable text remain"
    # Twitch must appear in the FIRST screenful of text, not only in the FAQ:
    # a model skimming the opening should learn what this is without the head.
    assert "twitch" in txt[:1200], "the opening text no longer says Twitch"


def test_every_public_page_still_carries_its_schema():
    """The redesign machine keeps running; the structured data must not fall
    off the truck. FAQ is derived from the live markup so it follows edits by
    construction — the assertion here is that each page still SHIPS its type."""
    import json, re
    from src.dashboard.api import LANDING_HTML
    from src.dashboard import tutorial_html, compare_html

    def types(html):
        out = []
        for b in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S):
            d = json.loads(b)                     # also: every blob must PARSE
            t = d.get("@type") or [x.get("@type") for x in d.get("@graph", [])]
            out.extend(t if isinstance(t, list) else [t])
        return out

    assert set(types(LANDING_HTML)) >= {"SoftwareApplication", "Organization",
                                        "WebSite"}
    assert "HowTo" in types(tutorial_html.render())
    assert "ItemList" in types(compare_html.render())



def test_no_faq_page_is_published_without_questions():
    """A PRE-EXISTING BUG, once: the FAQPage was built while one answer was
    still a placeholder and the page published a question with the empty
    string as its answer. The rule: never publish a FAQPage that is not
    backed by visible questions, and never one with an empty answer. The FAQ
    is back on the landing page (owner's ask, after v4), so the published
    FAQPage must carry every visible question and every answer must be
    real prose."""
    import json as _json, re as _re
    from src.dashboard.api import LANDING_HTML, _faq_schema
    blobs = _re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                        LANDING_HTML, _re.S)
    faq = [_json.loads(b) for b in blobs if '"FAQPage"' in b]
    assert len(faq) == 1, "exactly one FAQPage ships with the FAQ"
    items = faq[0]["mainEntity"]
    visible = LANDING_HTML.count('class="faq-q"')
    assert visible >= 12 and len(items) == visible, (len(items), visible)
    for it in items:
        assert it["name"].strip() and len(it["acceptedAnswer"]["text"]) > 40, it
        assert "<" not in it["acceptedAnswer"]["text"], "markup leaked into the schema"
    assert _faq_schema("<p>no questions here</p>") == ""