"""The blog and Highlightz Campaigns (owner, 2026-10-25: "Make sure our blog
page talks about our internal campaigns now too").

What these defend:
  1. while campaigns are off the blog is exactly what it was: no article, no
     mention, a 404 at its address (which the allowlist always lets through,
     as /promote's is);
  2. while they are on: an article about them, first in the guides, in the
     sitemap and the structured data, dated its own day; the two guides about
     finding paid campaigns mention it; every "Where Highlightz fits" says so;
  3. the part for streamers follows its own switch, and its fee and budget
     range are the campaign settings';
  4. it is honest about entering (posted from Highlightz, on Pro) and about
     results (no promised entries or views);
  5. the blog's own rules hold for the new copy: links resolve, outbound
     links are nofollow, every article is dated and sourced.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api
from src.dashboard import blog_content as B
from src.dashboard import blog_html as H

SLUG = B.CAMPAIGNS_SLUG
PATH = "/blog/" + SLUG
anon = TestClient(api.app)


@pytest.fixture
def flags(monkeypatch):
    def set_(campaigns=True, requests=True, uploads=True, pct=5.0, flat=0.0):
        s = api.settings
        monkeypatch.setattr(s, "campaigns_enabled", campaigns)
        monkeypatch.setattr(s, "campaign_requests_enabled", requests)
        monkeypatch.setattr(s, "uploads_enabled", uploads)
        monkeypatch.setattr(s, "campaign_fee_pct", pct)
        monkeypatch.setattr(s, "campaign_flat_fee_usd", flat)
        monkeypatch.setattr(s, "campaign_min_budget_usd", 100.0)
        monkeypatch.setattr(s, "campaign_max_budget_usd", 50000.0)
    return set_


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_while_campaigns_are_off_the_blog_is_unchanged(flags):
    flags(campaigns=False, requests=True)
    assert B.articles() is B.ARTICLES
    assert H.render_article(SLUG) is None
    assert PATH in api._OPEN_PATHS, "the allowlist is built at startup and must already know it"
    assert anon.get(PATH, follow_redirects=False).status_code == 404
    assert SLUG not in H.render_index() and PATH not in anon.get("/sitemap.xml").text
    assert "Four places to look" in H.render_article("find-streamer-campaigns")
    assert not any("Campaigns" in t for t, _ in B.highlightz_points())


def test_while_on_the_article_is_published_first(flags):
    flags()
    index = H.render_index()
    posts = re.search(r'<ul class="posts">(.*?)</ul>', index, re.S).group(1)
    assert re.findall(r'href="/blog/([\w-]+)"', posts)[0] == SLUG, "it is not the first guide"
    schema = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', index, re.S).group(1))
    post = next(p for p in schema["blogPost"] if p["url"].endswith(PATH))
    assert post["datePublished"] == B.CAMPAIGNS_PUBLISHED
    r = anon.get(PATH, follow_redirects=False)
    assert r.status_code == 200 and "Highlightz Campaigns: prize pools for clipping streams" in r.text
    xml = anon.get("/sitemap.xml").text
    assert "<loc>https://highlightz.app" + PATH + "</loc><lastmod>" in xml
    assert api._PAGE_SOURCE[PATH] == "src/dashboard/blog_content.py"


def test_it_is_dated_its_own_day_and_the_others_keep_theirs(flags):
    flags()
    html = H.render_article(SLUG)
    assert "Checked " + B.CAMPAIGNS_CHECKED in html and B.CHECKED_ON not in html.split('id="more"')[0]
    a = B.article(SLUG)
    srcs = re.search(r'<ol class="srcs">(.*?)</ol>', html, re.S).group(1)
    assert srcs.count("<li>") == len(a.sources) == 2
    assert "Checked " + B.CHECKED_ON in H.render_article("clipping-platforms")


def test_the_guides_about_paid_campaigns_mention_ours(flags):
    flags()
    find = H.render_article("find-streamer-campaigns")
    assert "Five places to look" in find and ">5. Highlightz Campaigns</h2>" in find
    assert 'href="' + PATH + '"' in find
    assert "Highlightz Campaigns" in B.article("find-streamer-campaigns").description
    money = H.render_article("how-clippers-make-money")
    assert "Some campaigns pay prizes instead of a rate per view" in money
    # Every "Where Highlightz fits", in every guide that has one, says so.
    for a in B.articles():
        if ("highlightz",) in a.blocks:
            fits = re.search(r'<ul class="hz">(.*?)</ul>', H.render_article(a.slug), re.S).group(1)
            assert "Campaigns with prize pools" in fits and 'href="' + PATH + '"' in fits, a.slug


def test_the_streamer_part_follows_its_own_switch(flags):
    flags(requests=False)
    html = H.render_article(SLUG)
    assert "Running a campaign for your stream" not in html and 'href="/promote"' not in html
    assert len(B.article(SLUG).sources) == 1
    flags(requests=True, pct=7.5, flat=25.0)
    html = H.render_article(SLUG)
    assert "our fee of $25 per campaign plus 7.5% of the budget" in html
    assert "from $100 to $50,000" in html and 'href="/promote"' in html


def test_it_is_honest_about_entering_and_results(flags):
    flags(uploads=True)
    text = _text(H.render_article(SLUG))
    assert "Highlightz, which is on Pro" in text
    assert "A clip posted somewhere else, outside Highlightz, is not an entry." in text
    assert "We do not promise any number of entries or views." in text
    assert "guarantee" not in text.lower()
    flags(uploads=False)
    assert "once posting opens on Pro" in _text(H.render_article(SLUG))
    assert "once posting opens on Pro" in _text(H.render_article("find-streamer-campaigns"))


def test_the_blogs_rules_hold_for_the_new_copy(flags):
    flags()
    known = set(B.paths()) | {"/promote", "/tos#campaigns"}
    for a in B.articles():
        texts = [b[1] for b in a.blocks if b[0] in ("p", "note")]
        texts += [x for b in a.blocks if b[0] == "list" for x in b[1]]
        texts += [d for _, d in B.highlightz_points()]
        for t in texts:
            for href in re.findall(r"\]\((/[^)]*)\)", t):
                assert href in known, f"{a.slug} links to {href}"
        html = H.render_article(a.slug)
        for tag in re.findall(r"<a [^>]*href=\"https?://[^\"]*\"[^>]*>", html):
            assert 'rel="nofollow noopener"' in tag, tag
        assert a.sources and a.checked in html, a.slug
    assert "/tos#campaigns" in known and 'id="campaigns"' in api.TOS_HTML
