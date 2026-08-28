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
    assert len(_graph()) >= 4
