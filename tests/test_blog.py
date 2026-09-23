"""/blog: guides to making money clipping.

The blog names five real companies and quotes their rates and rules, so
what is pinned here is the transparency the owner asked for ("I need it to
be transparent for the user"): every platform answers the same requirement
questions, unconfirmed answers say so, every claim is dated and sourced,
nothing unreleased is sold, and the Highlight secret stays one.
"""

import re

import pytest
from fastapi.testclient import TestClient

from src.dashboard import blog_content as B
from src.dashboard import blog_html as H
from src.dashboard.api import app

anon = TestClient(app)


def _all_pages() -> list[str]:
    return [H.render_index()] + [H.render_article(a.slug) for a in B.ARTICLES]


# ── routing ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path", B.paths())
def test_every_blog_page_is_served_to_a_signed_out_reader(path):
    r = anon.get(path, follow_redirects=False)
    assert r.status_code == 200, f"{path} -> {r.status_code}"
    assert "text/html" in r.headers["content-type"]


def test_the_blog_route_sits_above_the_catch_all():
    paths = [getattr(r, "path", "") for r in app.routes]
    assert paths.index("/blog") < paths.index("/{slug}"), \
        "/blog is declared below the catch-all and will never be reached"


def test_an_unknown_article_is_not_a_page():
    assert H.render_article("no-such-post") is None
    from src.dashboard import api
    assert "/blog/no-such-post" not in api._OPEN_PATHS


def test_the_blog_is_in_the_sitemap_with_its_source():
    from src.dashboard.api import _PAGE_SOURCE
    xml = anon.get("/sitemap.xml").text
    for p in B.paths():
        assert "<loc>https://highlightz.app" + p + "</loc>" in xml, p
        assert _PAGE_SOURCE[p] == "src/dashboard/blog_content.py"


# ── the tab ──────────────────────────────────────────────────────────────────

def test_the_blog_is_a_tab_on_every_public_bar_and_footer():
    from src.dashboard import compare_html, tutorial_html
    from src.dashboard.api import LANDING_HTML, TOS_HTML
    for name, html in (("landing", LANDING_HTML), ("compare", compare_html.render()),
                       ("tutorial", tutorial_html.render()), ("legal", TOS_HTML)):
        assert '<a href="/blog" class="nav-link">Blog</a>' in html, f"{name}: no Blog tab"
        assert '<a href="/compare">Compare</a><a href="/blog">Blog</a>' in html, \
            f"{name}: no Blog link in the footer"
    assert '<a href="/blog" class="nav-link on" aria-current="page">Blog</a>' in H.render_index()


def test_the_llm_brief_lists_the_blog():
    assert "https://highlightz.app/blog" in anon.get("/llms.txt").text


# ── transparency ─────────────────────────────────────────────────────────────

def test_there_are_five_platforms_with_unique_slugs_and_honest_fits():
    assert len(B.PLATFORMS) == 5
    assert len({p.slug for p in B.PLATFORMS}) == 5
    for p in B.PLATFORMS:
        assert p.fit in B.FITS, p.slug
        assert p.fit_why and p.tip and p.summary and p.who_posts and p.pay, p.slug


@pytest.mark.parametrize("p", B.PLATFORMS, ids=lambda p: p.slug)
def test_every_platform_answers_every_requirement_in_the_same_order(p):
    assert tuple(k for k, _ in p.reqs) == B.REQ_FIELDS, \
        f"{p.slug} answers a different set of questions, so it cannot be compared"
    for k, v in p.reqs:
        assert v.strip(), f"{p.slug}: '{k}' is blank — write NOT_PUBLISHED instead"


@pytest.mark.parametrize("p", B.PLATFORMS, ids=lambda p: p.slug)
def test_every_platform_renders_its_requirements_and_dated_sources(p):
    html = H.render_article("clipping-platforms")
    sec = re.search(r'<section class="plat[^"]*" id="' + p.slug + r'">(.*?)</section>', html, re.S)
    assert sec, f"{p.slug} has no profile on the page"
    body = sec.group(1)
    for k in B.REQ_FIELDS:
        assert ">" + k + "<" in body, f"{p.slug}: '{k}' missing from the page"
    assert "Checked " + B.CHECKED_ON in body
    assert len(p.sources) >= 3, f"{p.slug} rests on too few sources"
    for s in p.sources:
        assert s.url.startswith("https://")
        assert 'href="' + s.url + '"' in body, f"{p.slug}: a source is not linked"


def test_an_unconfirmed_answer_is_shown_as_such_not_hidden():
    html = H.render_article("clipping-platforms")
    n = sum(v == B.NOT_PUBLISHED for p in B.PLATFORMS for _, v in p.reqs)
    assert n, "expected at least one unconfirmed answer in the data"
    assert html.count('<span class="np">Not published</span>') == n


def test_every_article_is_dated_and_sourced():
    for a in B.ARTICLES:
        html = H.render_article(a.slug)
        assert B.CHECKED_ON in html, a.slug
        assert a.sources, a.slug
        srcs = re.search(r'<ol class="srcs">(.*?)</ol>', html, re.S).group(1)
        assert srcs.count("<li>") == len(a.sources), a.slug


def test_every_outbound_link_is_nofollow():
    for html in _all_pages():
        for tag in re.findall(r"<a [^>]*href=\"https?://[^\"]*\"[^>]*>", html):
            assert 'rel="nofollow noopener"' in tag, f"followed outbound link: {tag}"


def test_inline_links_only_point_at_real_articles():
    known = set(B.paths())
    for a in B.ARTICLES:
        texts = [b[1] for b in a.blocks if b[0] in ("p", "note")]
        texts += [x for b in a.blocks if b[0] == "list" for x in b[1]]
        for t in texts:
            for href in re.findall(r"\]\((/[^)]*)\)", t):
                assert href in known, f"{a.slug} links to {href}, which is not a page"


def test_the_chart_states_its_numbers_as_text_too():
    html = H.render_article("how-clippers-make-money")
    for label, lo, hi, _ in B.PAY_PER_1K:
        rng = "$" + format(lo, ".2f") + "–$" + format(hi, ".2f")
        assert html.count(rng) >= 2, f"{label}: shown on the bar but not in the table"
        assert lo < hi <= 6.0, f"{label} falls off the chart's $0–$6 scale"


# ── what we claim about ourselves ────────────────────────────────────────────

def test_plan_numbers_come_from_the_real_plans():
    from src.billing.plans import PLAN_LIMITS
    text = " ".join(d for _, d in B.highlightz_points())
    for plan in ("free", "starter", "pro"):
        n = PLAN_LIMITS[plan]["max_streams"]
        assert f"{n} on {plan.capitalize()}" in text, plan


@pytest.mark.parametrize("shipped", [False, True])
def test_the_editor_and_scheduler_are_never_sold_before_release(monkeypatch, shipped):
    from src.dashboard import compare_content
    monkeypatch.setattr(compare_content, "_shipped", lambda: shipped)
    html = H.render_article("how-clippers-make-money")
    if shipped:
        assert "The Clip Editor turns a clip vertical" in html
    else:
        assert "Editing and scheduling are coming" in html
        assert "The Clip Editor turns a clip vertical" not in html


def test_the_highlight_mechanism_is_never_described():
    """How Highlight clips are found is the owner's secret. The blog talks
    about views all day — that is what clippers are paid for — so the check
    is on everything said about Highlight clips, not the whole page."""
    said = " ".join(t + " " + d for t, d in B.highlightz_points()
                    if "highlight" in (t + d).lower()).lower()
    assert "highlight" in said
    for tell in ("viewer", "audience", "crowd", "clipped", "cluster", "spike",
                 "views", "chat", "trending", "interest"):
        assert tell not in said, f"the Highlight copy gives away the mechanism: {tell!r}"
    for html in _all_pages():
        low = html.lower()
        for tell in ("audience clipping", "viewers clip", "clip spike", "crowd-sourced"):
            assert tell not in low, tell


# ── pictures and logos ───────────────────────────────────────────────────────

def test_every_picture_is_a_shipped_product_screen():
    from src.dashboard.api import _STATIC_DIR
    for file, alt in B.SHOTS.values():
        assert (_STATIC_DIR / "landing" / file).is_file(), file
        assert alt
    for html in _all_pages():
        for img in re.findall(r'<img [^>]*src="(/static/[^"]+)"', html):
            assert (_STATIC_DIR / img[len("/static/"):]).is_file(), img


def test_a_missing_logo_falls_back_to_a_tile_not_a_broken_image(monkeypatch, tmp_path):
    monkeypatch.setattr(H, "_LOGO_DIR", tmp_path)
    html = H.render_index()
    assert html.count('class="logo mono"') == len(B.PLATFORMS)
    (tmp_path / "whop.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    html = H.render_index()
    assert '<img src="/static/blog/logos/whop.svg" alt="Whop Content Rewards logo"' in html
    assert html.count('class="logo mono"') == len(B.PLATFORMS) - 1


# ── page hygiene ─────────────────────────────────────────────────────────────

def test_every_page_carries_parseable_blog_schema():
    import json
    for html in _all_pages():
        blobs = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
        assert blobs
        types = {json.loads(b)["@type"] for b in blobs}
        assert types & {"Blog", "BlogPosting"}


def test_every_h2_is_in_the_display_voice_outside_the_article_body():
    for html in _all_pages():
        body = re.sub(r'<div class="art-body">.*?</main>', "", html, flags=re.S)
        assert body.count('<h2 class="disp">') == body.count("<h2 "), \
            "an h2 is set outside the display voice"
