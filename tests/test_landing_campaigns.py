"""Campaigns advertised on the landing page (owner, 2026-10-25: "make it so
that its advertised on the landing page now as well").

What these defend:
  1. the line above the first headline and the block in Pricing appear only while
     streamers can ask for a campaign (the nav tab's switch), and the half
     for clippers only while the marketplace is open to them;
  2. they keep the page's shape: still eight sections in the same order on
     the same grounds, every anchor resolves, and the copy keeps the page's
     cadence rules (no em dashes, no "no X, no Y", no AI claims);
  3. the fee and the budget range are the campaign settings', as on /promote;
  4. the copy says how clippers enter (post it yourself and add its link, or
     post it from Highlightz on Pro), and the campaign page offers both;
  5. "Browse the campaigns" signs a clipper in and lands on Campaigns.
"""

import re

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api


@pytest.fixture
def flags(monkeypatch):
    def set_(requests=True, campaigns=True, uploads=True, pct=5.0, flat=0.0):
        monkeypatch.setattr(api.settings, "campaign_requests_enabled", requests)
        monkeypatch.setattr(api.settings, "campaigns_enabled", campaigns)
        monkeypatch.setattr(api.settings, "uploads_enabled", uploads)
        monkeypatch.setattr(api.settings, "campaign_fee_pct", pct)
        monkeypatch.setattr(api.settings, "campaign_flat_fee_usd", flat)
        monkeypatch.setattr(api.settings, "campaign_min_budget_usd", 100.0)
        monkeypatch.setattr(api.settings, "campaign_max_budget_usd", 50000.0)
        return api.render_landing()
    return set_


def _block(html: str) -> str:
    return re.search(r'<div class="edit" id="campaigns">.*?<p class="edit-cta">.*?</p></div>',
                     html, re.S).group(0)


def test_nothing_shows_while_streamers_cannot_ask(flags):
    html = flags(requests=False, campaigns=True)
    assert 'id="campaigns"' not in html and 'class="proof-new"' not in html
    assert 'href="#campaigns"' not in html
    assert "<!--CAMPAIGNS-->" not in html and "<!--PROOF_CAMPAIGNS-->" not in html


def test_the_top_line_leads_to_the_block_in_pricing(flags):
    """The line sat on the cover under the name; with the cover gone
    (2026-10-26) it sits in the first screen, just above the headline."""
    html = flags()
    assert html.count('href="#campaigns"') == 1 and html.count('id="campaigns"') == 1
    pricing = html[html.index('<section class="light pricing" id="pricing"'):html.index('id="faq"')]
    assert 'id="campaigns"' in pricing, "the block left Pricing"
    line = html.index('class="proof-new"')
    assert html.index('id="proof"') < line < html.index('<h1 '), \
        "the line is not in the first screen above the headline"


def test_the_page_keeps_its_shape(flags):
    html = flags()
    secs = re.findall(r'<section class="([^"]*)" id="([^"]+)"', html)
    assert [i for _, i in secs] == ["proof", "numbers", "catches", "score", "watch", "pricing", "faq", "start"]
    assert ["light" in c.split() for c, _ in secs] == [False, False, True, True, False, True, True, False]
    ids = set(re.findall(r'id="([^"]+)"', html))
    for href in set(re.findall(r'href="#([^"]+)"', html)):
        assert href in ids, href


def test_the_copy_keeps_the_pages_rules(flags):
    text = re.sub(r"<[^>]+>", " ", _block(flags()) + api._proof_campaigns())
    assert "—" not in text and "&mdash;" not in text
    assert not re.search(r"[Nn]ot a |[Nn]o [a-z]+, no [a-z]+", text)
    low = text.lower()
    for phrase in ("ai-powered", "powered by ai", "our ai", "machine learning", "guarantee"):
        assert phrase not in low, phrase


def test_the_fee_and_range_come_from_the_settings(flags):
    block = _block(flags(pct=7.5, flat=25.0))
    assert "our fee of $25 per campaign plus 7.5% of the budget is added on top" in block
    assert "$100 to $50,000" in block
    block = _block(flags(pct=5.0))
    assert "our fee of 5% of the budget is added on top" in block


def test_the_clippers_half_follows_the_marketplace_switch(flags):
    block = _block(flags(campaigns=True))
    assert 'href="/login?next=campaigns"' in block and "Clippers play for the pool" in block
    assert "post it yourself and add its link, or post it straight from Highlightz on Pro" in block
    block = _block(flags(campaigns=False))
    assert "next=campaigns" not in block and "Clippers play" not in block
    assert 'href="/promote"' in block
    held = _block(flags(uploads=False))
    assert "then post it yourself and add its link." in held


def test_browse_the_campaigns_signs_in_and_lands_on_campaigns(monkeypatch):
    assert api._after_sign_in("campaigns") == "/?view=campaigns"
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    deep = page[page.index("_params.get('view') === 'campaigns'"):][:300]
    assert "setRoute('campaigns')" in deep and "history.replaceState" in deep
    gate = page[page.index("const promoting = "):page.index("return <FirstRun")]
    assert "route === 'campaigns' && canCampaigns" in gate


def test_the_campaign_page_offers_both_ways_in():
    """Owner, 2026-10-25: "let them post their entry links if they want ...
    dont make it forced". Post it yourself and add the link, or post it from
    Highlightz with Pro."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    camp_page = page[page.index("function CampaignPage("):page.index("function CampaignsScreen(")]
    assert "Post your clip yourself and add its link under Your entries" in camp_page
    assert "straight from Highlightz with Pro." in camp_page
    assert 'href="/billing/paywall"' in camp_page
    assert "postingOpen={uploadsOn}" in page and "postingOpen={postingOpen}" in page


def test_the_landing_route_serves_it(flags):
    flags()
    html = TestClient(api.app, base_url="https://testserver").get("/").text
    assert 'id="campaigns"' in html and "Get your stream clipped." in html
