"""The comparison page.

This is the only page on the site that makes factual claims about other
companies, which makes a stale number here a different class of problem from a
stale number anywhere else. Two things are therefore enforced rather than
trusted:

  * Every competitor price carries a source and a date, and the page shows them.
  * Our own numbers are pinned to src/billing/plans.py. A comparison page whose
    headline is "we cost less" must not be the last place in the codebase to
    hear that a price changed.
"""

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api, compare_content as C


@pytest.fixture
def client():
    return TestClient(api.app)


@pytest.fixture
def page():
    from src.dashboard.compare_html import render
    return render()


# ── it is reachable, and reachable by a stranger ─────────────────────────────

def test_the_page_serves(client):
    r = client.get("/compare")
    assert r.status_code == 200
    assert "Opus Clip" in r.text and "Eklipse" in r.text


def test_a_signed_out_visitor_can_read_it(client):
    """The entire audience is people who have not signed up. Behind the auth
    middleware it would be a comparison page nobody comparing can reach."""
    r = client.get("/compare", follow_redirects=False)
    assert r.status_code == 200, "the comparison page redirects to login"
    assert "/compare" in api._OPEN_PATHS


def test_it_is_declared_above_the_catch_all():
    """`/{slug}` matches any single-segment path and FastAPI resolves in
    declaration order, so a route registered after it never runs."""
    paths = [getattr(r, "path", "") for r in api.app.routes]
    assert paths.index("/compare") < paths.index("/{slug}")


def test_it_is_linked_and_indexed(client):
    from src.dashboard.api import LANDING_HTML
    from src.dashboard.tutorial_html import render as tut
    assert 'href="/compare"' in LANDING_HTML, "not linked from the landing page"
    assert 'href="/compare"' in tut(), "not linked from the tutorial"
    assert "/compare" in client.get("/sitemap.xml").text, "not in the sitemap"


# ── the claims about other companies ─────────────────────────────────────────

def test_every_competitor_price_is_sourced_and_dated(page):
    """An undated price about a named company is an assertion with nothing
    behind it. Both the source link and the date must reach the page."""
    for product in C.PRODUCTS:
        if product.is_us:
            continue
        assert product.source_url.startswith("https://"), \
            f"{product.name} has no source URL"
        assert product.checked_on, f"{product.name} has no checked-on date"
        assert product.source_url in page, f"{product.name}'s source is not linked"
        assert product.checked_on in page, f"{product.name}'s date is not shown"
        assert product.plans, f"{product.name} has no plans to compare"


def test_outbound_competitor_links_do_not_pass_ranking(page):
    """Linking competitors is the honest thing to do; handing them SEO for it
    is not required."""
    for product in C.PRODUCTS:
        if product.is_us:
            continue
        i = page.index(product.source_url)
        tag = page[page.rindex("<a", 0, i):page.index(">", i)]
        assert 'rel="nofollow noopener"' in tag, f"{product.name} link lacks rel"
        assert 'target="_blank"' in tag


def test_unconfirmed_prices_say_so_on_the_page(page):
    """While PRICES_CONFIRMED is False the page quotes figures nobody has
    checked against the source. The reader is entitled to know that."""
    if not C.PRICES_CONFIRMED:
        assert "not yet re-verified" in page.lower() or "not been confirmed" in page.lower(), \
            "unverified prices are presented as fact"


def test_confirming_prices_removes_the_caveat(monkeypatch):
    """The flag has to actually do something, or it is decoration."""
    monkeypatch.setattr(C, "PRICES_CONFIRMED", True)
    from src.dashboard.compare_html import render
    assert "not yet re-verified" not in render().lower()


# ── our own numbers, pinned to the source of truth ───────────────────────────

def test_our_prices_match_the_real_plans():
    """The failure this prevents: Pro goes to $30 and the page that shouts
    about price keeps saying $25."""
    from src.billing.plans import PLAN_LIMITS
    ours = {p.name.lower(): p for p in C.HIGHLIGHTZ.plans}
    assert f"${PLAN_LIMITS['starter']['price']}/mo" == ours["starter"].price
    assert f"${PLAN_LIMITS['pro']['price']}/mo" == ours["pro"].price


def test_our_channel_counts_match_the_real_limits():
    from src.billing.plans import PLAN_LIMITS
    ours = {p.name.lower(): p for p in C.HIGHLIGHTZ.plans}
    assert str(PLAN_LIMITS["starter"]["max_streams"]) in ours["starter"].note
    assert str(PLAN_LIMITS["pro"]["max_streams"]) in ours["pro"].note
    assert str(PLAN_LIMITS["pro"]["max_pending"]) in ours["pro"].note


def test_the_advertised_trial_length_is_the_real_one():
    from src.billing.plans import TRIAL_DAYS
    trial = [p for p in C.HIGHLIGHTZ.plans if "trial" in p.name.lower()][0]
    assert str(TRIAL_DAYS) in trial.note
    assert "no credit card" in trial.note.lower()


def test_we_do_not_claim_a_feature_our_plans_do_not_have():
    """VOD is a Pro entitlement. If that ever stops being true the page must
    stop selling it."""
    from src.billing.plans import PLAN_LIMITS
    pro = [p for p in C.HIGHLIGHTZ.plans if p.name.lower() == "pro"][0]
    if "vod" in pro.note.lower():
        assert PLAN_LIMITS["pro"]["vod"] is True


# ── honesty, which is what makes the rest credible ───────────────────────────

def test_the_page_says_where_the_competition_is_better(page):
    """Deliberate, not decorative. A reader weighing three products already
    knows these tools do things we do not; a page that pretends otherwise
    reads as marketing and taints the claims that are true."""
    assert C.THEY_DO_BETTER["points"], "the where-they-win section is empty"
    for title, _ in C.THEY_DO_BETTER["points"]:
        assert title in page
    assert "buy theirs" in page.lower()


def test_the_matrix_does_not_claim_a_clean_sweep():
    """Every row favouring us is the signature of a page nobody believes."""
    theirs = [row for row in C.FEATURES if row[2] is True or row[3] is True]
    assert len(theirs) >= 2, "no row credits a competitor with anything"


def test_features_that_are_not_yes_or_no_are_not_rendered_as_yes():
    """"3-10 channels", "VOD only" and "7 days" are qualified answers. Turning
    them into a tick overstates our side."""
    from src.dashboard.compare_html import _cell
    assert "Yes" not in _cell("VOD only", "Highlightz")
    assert "VOD only" in _cell("VOD only", "Highlightz")
    assert "Yes" in _cell(True, "Highlightz")


def test_every_matrix_row_explains_itself():
    """A bare tick is an assertion. The explanation is what makes it checkable."""
    for row in C.FEATURES:
        feat, _, _, _, why = row
        assert len(why) > 40, f"row {feat!r} has no real explanation"


# ── the mobile layout, which is where this gets shared ───────────────────────

def test_the_matrix_stacks_instead_of_scrolling_on_phones(page):
    """A comparison table you can only read one column at a time is not a
    comparison. Verified visually at 390px; this stops the rule being deleted."""
    assert "data-l=" in page, "cells carry no label for the stacked layout"
    assert "attr(data-l)" in page, "the stacked layout never shows which column is which"
    assert ".mwrap{overflow-x:visible}" in page.replace("\n", "").replace("  ", "")


def test_the_page_renders_without_a_bundler(page):
    """Same constraint as the rest of the site: no build step, no external
    fetches beyond the fonts already self-hosted."""
    assert "<script src=" not in page, "pulled in an external script"
    assert page.count("<style>") == 1
