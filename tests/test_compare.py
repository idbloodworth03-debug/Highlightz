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


def test_unconfirmed_prices_say_so_on_the_page(monkeypatch):
    """The flag has to work in the direction that matters. Prices are confirmed
    today, so the live page carries no caveat — but the moment somebody edits a
    figure they have not checked and drops the flag, the page must say so
    rather than presenting it as fact."""
    monkeypatch.setattr(C, "PRICES_CONFIRMED", False)
    from src.dashboard.compare_html import render
    assert "not yet re-verified" in render().lower(), \
        "unverified prices would be presented as fact"


def test_the_confirmed_page_carries_no_caveat(page):
    assert "not yet re-verified" not in page.lower()
    assert C.PRICES_CONFIRMED, "prices are marked unconfirmed"


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


def test_the_advertised_free_plan_is_the_real_one():
    """Was `..._trial_length_is_the_real_one`. The entry plan is a standing
    free tier again rather than a countdown, so what has to match the code is
    its LIMITS instead of its duration."""
    from src.billing.plans import PLAN_LIMITS
    free_plan = [p for p in C.HIGHLIGHTZ.plans if p.name.lower() == "free"][0]
    assert free_plan.price == "$0"
    note = free_plan.note.lower()
    assert str(PLAN_LIMITS["free"]["max_streams"]) in note
    assert str(PLAN_LIMITS["free"]["max_pending"]) in note
    assert "no card" in note, "the compare page does not say a card is not needed"
    assert "no time limit" in note, "the compare page implies the free plan expires"


def test_we_do_not_claim_a_feature_our_plans_do_not_have():
    """VOD is a Pro entitlement. If that ever stops being true the page must
    stop selling it."""
    from src.billing.plans import PLAN_LIMITS
    pro = [p for p in C.HIGHLIGHTZ.plans if p.name.lower() == "pro"][0]
    if "vod" in pro.note.lower():
        assert PLAN_LIMITS["pro"]["vod"] is True


# ── the credits: the price that only shows up after signing up ──────────────

def test_the_credit_rules_are_on_the_page_with_their_sources_and_date(page):
    """Owner: "they also charge for credits as well not just subscription".
    The section states what each product meters, what the plan includes,
    what happens when it runs out, whether unused allowance survives, and
    the paid extras — and, like every other competitor claim here, every
    figure is dated and linked to the company's own help page."""
    from src.billing.plans import PLAN_LIMITS
    assert 'id="credits"' in page
    for label, *_ in C.CREDITS["rows"]:
        assert label in page, f"credit row {label!r} is not on the page"
    assert C.CREDITS_CHECKED_ON in page, "the credit figures carry no date"
    for title, url in C.CREDITS["sources"]:
        assert url.startswith("https://") and ("opus.pro" in url or "eklipse.gg" in url)
        i = page.index(url)
        tag = page[page.rindex("<a", 0, i):page.index(">", i)]
        assert 'rel="nofollow noopener"' in tag and 'target="_blank"' in tag, title
    # The competitor figures that were researched, verbatim.
    for fact in ("One credit is one minute", "300 credits and two seats",
                 "expire after 60 days", "$39.98 a month for 1,200 minutes",
                 "$18.99 each, three for $49.99, seven for $99.99", "VIP Pass"):
        assert fact in page, f"the credit section lost: {fact!r}"
    # And our column says the same thing on every row: nothing is metered.
    ours = [row[1] for row in C.CREDITS["rows"]]
    assert ours[0].startswith("Nothing.") and "does not run out" in ours[2]
    assert str(PLAN_LIMITS["pro"]["max_streams"]) in ours[1], \
        "our channel count in the credit section is typed, not read"


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


def test_the_big_titles_are_the_display_voice_like_the_rest_of_the_site():
    """The landing v4 rebuild made the display voice the sans at its heaviest
    weight, and the walkthrough and this page followed (owner: "do the compare
    page too so it matches"). A comparison page still setting its headings in
    the script face is the same site in two typographic voices."""
    import re
    from src.dashboard import compare_html
    html = compare_html.render()
    css = compare_html._CSS
    assert "Lobster" not in html, "the script face is back on /compare"
    assert re.search(r"\.disp\{font-family:var\(--sans\);font-weight:800", css), \
        "the display voice is not defined on this page"
    for tag in ('<h1 class="disp">', '<h2 class="disp">'):
        assert tag in html, f"a big title is not in the display voice: {tag}"
    assert html.count('<h2 class="disp">') == html.count("<h2 "), \
        "an h2 is set outside the display voice"


def test_the_page_shares_the_landing_page_s_bar_and_footer():
    """One header, one footer, across the site: the bar carries the landing
    page's links in the landing page's order with this page marked current,
    and the footer is the landing page's one-row footer."""
    from src.dashboard import compare_html
    from src.dashboard.api import LANDING_HTML
    html = compare_html.render()
    for href in ("/#catches", "/#score", "/#watch", "/#pricing", "/#faq", "/tutorial"):
        assert 'href="' + href + '" class="nav-link"' in html, f"the bar lost {href}"
    assert '<a href="/compare" class="nav-link on" aria-current="page">Compare</a>' in html
    assert '<a href="/login" class="btn btn-go">Get started</a>' in html
    assert '<span class="fl">&copy; 2026 ANTI Technology LLC</span>' in html
    assert '<span class="fl">&copy; 2026 ANTI Technology LLC</span>' in LANDING_HTML
    assert 'rel="preload" href="/static/fonts/sora-var.woff2"' in html, \
        "the display face is used above the fold but not preloaded"


# ── held-back features are never advertised as available ────────────────────
#
# THE BUG THIS EXISTS TO PREVENT (found 2026-09-21). The Clip Editor and the
# Scheduler were built, then held behind UPLOADS_ENABLED. The landing page
# and the dashboard both honoured the flag. /compare and /llms.txt did not —
# they went on describing both in the present tense as things a reader could
# go and use, for weeks.
#
# A comparison page is the worst surface in the site to be wrong on: the
# reader who signs up to get the feature is the reader who asks for a refund.
# /llms.txt is arguably worse, because a model that reads it repeats the
# claim to people who never visit the site at all.
#
# The owner's instruction was to keep the pitch and drop the claim — "I dont
# want it released yet but I want the talks about it" — so these tests check
# for the CLAIM, not for the mention.

import os
import subprocess
import sys


def _render_with_flag(flag: str, snippet: str) -> str:
    """Import the public-copy modules fresh with UPLOADS_ENABLED=flag.

    A subprocess because both modules compute their copy at import time (the
    same way the landing page is built once and rebuilt on restart), so the
    flag cannot be flipped inside a running interpreter.
    """
    env = dict(os.environ, UPLOADS_ENABLED=flag)
    out = subprocess.run([sys.executable, "-c", snippet], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout


_MATRIX = """
from src.dashboard import compare_content as C
for f in C.FEATURES:
    print(repr(f[1]), '|', f[0])
"""

_LLMS = """
import asyncio
from src.dashboard import api
t = asyncio.run(api.llms_full_txt())
print(t.body.decode() if hasattr(t, 'body') else str(t))
"""


def test_compare_marks_the_editor_and_scheduler_soon_while_they_are_held_back():
    rows = _render_with_flag("false", _MATRIX)
    for feature in ("Vertical reframing and auto-captions",
                    "Auto-posts to TikTok, Shorts and Reels"):
        line = next(l for l in rows.splitlines() if l.endswith(feature))
        assert line.startswith("'Soon'"), \
            f"/compare still ticks {feature!r} while UPLOADS_ENABLED is off: {line}"


def test_compare_ticks_them_once_they_really_ship():
    """The other half: the caveat has to disappear on release, or the page
    undersells a feature that is live."""
    rows = _render_with_flag("true", _MATRIX)
    for feature in ("Vertical reframing and auto-captions",
                    "Auto-posts to TikTok, Shorts and Reels"):
        line = next(l for l in rows.splitlines() if l.endswith(feature))
        assert line.startswith("True"), \
            f"/compare does not tick {feature!r} even when released: {line}"


def test_compare_still_talks_about_them_while_they_are_held_back():
    """Owner: keep the pitch, drop the claim. A row that vanished would lose
    the argument as well as the overclaim."""
    from src.dashboard import compare_content as C
    body = " ".join(str(x) for f in C.FEATURES for x in f)
    assert "Clip Editor" in body and "Scheduler" in body


def test_the_plan_table_in_llms_full_never_says_yes_before_release():
    """It read PLAN_LIMITS["uploads"] and printed Yes — so Pro was advertised
    a feature Pro does not currently get. _released() is the three-state
    answer the landing page already used."""
    txt = _render_with_flag("false", _LLMS)
    for feature in ("Clip Editor", "Scheduler", "Autopilot"):
        row = next(l for l in txt.splitlines() if l.startswith(f"| {feature} |"))
        assert "Yes" not in row, f"/llms-full.txt advertises {feature}: {row}"
        assert "Soon" in row, f"/llms-full.txt dropped {feature} entirely: {row}"


def test_the_plan_table_says_yes_once_released():
    txt = _render_with_flag("true", _LLMS)
    for feature in ("Clip Editor", "Scheduler", "Autopilot"):
        row = next(l for l in txt.splitlines() if l.startswith(f"| {feature} |"))
        assert "Yes" in row, f"/llms-full.txt hides a released {feature}: {row}"
