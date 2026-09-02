"""The public pages must describe the offer that actually exists.

WAS `test_no_free_tier_claim.py`. That file banned every mention of a free tier,
because there was not one: new signups got 7 days of Pro with a card up front
and then `locked`, while the landing page still sold "1 channel free, 15 pending
free" and the tutorial said "Highlightz is free to start and stays free". Those
were not marketing drift — the tutorial told a cancelling customer they would
land somewhere they would not.

THE OFFER HAS CHANGED BACK and so has this file. Free is a standing tier again:
no card, no clock, 1 channel and a 20-clip queue. The ban is inverted into a
requirement, and the tests that pinned the trial's wording now pin the free
plan's. Nothing here was deleted for being inconvenient; each assertion moved to
the other side of the same question.

WHY THE STRUCTURE SURVIVED THE REVERSAL. The durable part was never which claim
was true — it was that the copy and the code had nothing tying them together, so
either could move without the other. Every number below is still derived from
PLAN_LIMITS, so changing a plan breaks a test rather than quietly making a page
lie. That property is what has to outlast the next pricing change too.
"""

import re

import pytest

from src.billing.plans import PLAN_LIMITS


def _public_copy() -> dict:
    from src.dashboard.api import LANDING_HTML
    from src.dashboard.tutorial_html import render
    return {
        "landing": LANDING_HTML,
        "tutorial": render(),
    }


def _public_pages() -> dict:
    from src.dashboard.api import LANDING_HTML, LOGIN_HTML
    return {"landing": LANDING_HTML, "login": LOGIN_HTML}


# ── the offer, said the same way everywhere ──────────────────────────────────
# TWO ATOMS, and the reasoning is inherited from the version of this file that
# pinned the trial. "no card" is the part that changes what a visitor has to do
# to get started; "no time limit" is the part that makes it worth doing. A page
# with only the first reads as a lead magnet with a countdown behind it; a page
# with only the second leaves them expecting a card form. Both, everywhere.

_OFFER = "no card"
_ESCAPE = "no time limit"

# What must never appear again. Every one of these was live on some page while
# the trial was the only way in, and each is now FALSE — the most damaging kind
# of stale copy, because somebody reads it, signs up expecting a card form or a
# countdown, and finds the product behaving differently.
# REGEXES, NOT SUBSTRINGS, and one of them is why. The plain string "card
# required" appears inside "NO card required" — the new copy's own wording — so
# a substring ban fails on the very page it is meant to bless. What is banned is
# the AFFIRMATIVE claim, hence the negative lookbehind.
_DEAD_CLAIMS = (
    r"7 days free",
    r"cancel before day 7",
    r"(?<!no )card required",
    r"days of full pro",
    r"\$0 for 7 days",
    r"start your 7 days",
)


@pytest.mark.parametrize("claim", _DEAD_CLAIMS)
def test_the_retired_trial_is_not_still_being_advertised(claim):
    for name, html in _public_pages().items():
        hit = re.search(claim, html.lower())
        assert hit is None, f"{name} still advertises: {hit.group(0)!r}"


@pytest.mark.parametrize("claim", _DEAD_CLAIMS)
def test_the_tutorial_does_not_advertise_it_either(claim):
    from src.dashboard.tutorial_html import render
    hit = re.search(claim, render().lower())
    assert hit is None, f"the tutorial still advertises: {hit.group(0)!r}"


def test_the_ban_can_actually_catch_the_claim_it_is_for():
    """The lookbehind above is doing real work, so it gets checked: it has to
    reject a page that demands a card while allowing one that says it does
    not. A ban that cannot fire is not a ban."""
    assert re.search(r"(?<!no )card required", "signup: card required now")
    assert not re.search(r"(?<!no )card required", "free to start, no card required")


def test_every_public_page_states_the_offer_in_one_wording():
    for name, html in _public_pages().items():
        low = html.lower()
        assert _OFFER in low, f"{name} does not say the card is not needed"
        assert _ESCAPE in low, f"{name} does not say it has no time limit"


def test_the_offer_is_stated_before_a_visitor_is_asked_to_pay():
    """It used to be in the hero, above the fold. The hero's lede was removed
    on the owner's instruction, and the offer went with it — so the terms now
    first appear in the pricing section.

    KNOWN TRADE-OFF, recorded rather than quietly accepted: a visitor no
    longer meets "free to start, no card" until they scroll to pricing. What
    this still guarantees is the part that actually protects them — the terms
    are stated BEFORE the plans, not in a footnote after them, so nobody
    reaches a price without having read that there is a free way in."""
    from src.dashboard.api import LANDING_HTML
    low = LANDING_HTML.lower()
    pricing = low.index('id="pricing"')
    section = low[pricing:low.index('id="start"')]
    assert _OFFER in section, "pricing does not mention the card"
    assert _ESCAPE in section, "pricing does not say it is not a trial"
    # and the free plan is the first tier a reader meets, not an afterthought
    assert section.index("free") < section.index("$25"), \
        "the paid tiers are introduced before the free one"


def test_the_offer_is_not_set_in_the_faintest_ink_on_the_page():
    """It was 12px of --ink-3, the dimmest step in the palette, which is where
    you put a footnote. The .hero-note this used to read is gone with the hero;
    the offer now lives in the pricing lead, so that is what gets checked."""
    from src.dashboard.api import LANDING_HTML
    rule = re.search(r"\.price-lead\{([^}]*)\}", LANDING_HTML).group(1)
    assert "var(--ink-3)" not in rule, "the offer is set in the muted ink"
    size = re.search(r"font-size:(?:clamp\()?(\d+)px", rule)
    assert size and int(size.group(1)) >= 13, \
        f"the offer is set at {size.group(1) if size else '?'}px"


def test_the_queue_numbers_quoted_match_the_plans():
    """Free's queue is in the pricing lead; Starter's and Pro's are rows of
    their columns. All three read PLAN_LIMITS."""
    from src.dashboard.api import LANDING_HTML
    assert f"{PLAN_LIMITS['free']['max_pending']} clips held for review" in LANDING_HTML
    assert (f"<span>Clips held for review</span><b>{PLAN_LIMITS['starter']['max_pending']}</b>"
            in LANDING_HTML)
    assert (f"<span>Clips held for review</span><b>{PLAN_LIMITS['pro']['max_pending']}</b>"
            in LANDING_HTML)


def test_the_pricing_block_quotes_the_free_plan_from_the_code():
    """It leads the page now, so its numbers are the ones a visitor acts on."""
    from src.dashboard.api import _pricing
    html = _pricing()
    free = PLAN_LIMITS["free"]
    assert str(free["max_pending"]) in html
    assert str(free["max_suggested"]) in html


def test_the_tutorial_plan_table_leads_with_free_and_derives_its_numbers():
    from src.dashboard.tutorial_content import PLAN_ROWS
    head = PLAN_ROWS[0]
    assert head[1] == "Free", f"the plan table no longer leads with Free: {head}"
    rows = {r[0]: r for r in PLAN_ROWS[1:]}
    assert rows["Channels at once"][1] == str(PLAN_LIMITS["free"]["max_streams"])
    assert rows["Clips held for review"][1] == str(PLAN_LIMITS["free"]["max_pending"])
    assert rows["Highlight clips"][1] == str(PLAN_LIMITS["free"]["max_suggested"])
    assert rows["VOD Scanner"][1] == "No", "the tutorial gives free the VOD scanner"


# ── the claims have to be true, not merely present ───────────────────────────

def test_free_really_is_reachable_without_paying_anything():
    """The page says no card. This is the code agreeing."""
    from src.billing.plans import get_plan, limits_for
    fresh = {"id": "n", "subscription_status": "none"}
    assert get_plan(fresh) == "free"
    assert limits_for(fresh)["max_streams"] >= 1
    assert limits_for(fresh)["max_pending"] >= 1


def test_a_cancelling_user_really_does_land_on_free():
    """The inverse of `..._really_does_land_on_locked`, which was the fact
    behind the old tutorial edit. Cancelling is a soft landing again, so the
    tutorial is allowed to say so."""
    from src.billing.plans import get_plan, limits_for
    cancelled = {"id": "n", "subscription_status": "canceled"}
    assert get_plan(cancelled) == "free"
    assert limits_for(cancelled)["max_streams"] == PLAN_LIMITS["free"]["max_streams"]


def test_clips_really_do_survive_a_lapse():
    """The tutorial promises the library stays. GET /clips has no plan gate, so
    a free user can still read everything they kept."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.list_clips)
    assert "limits_for" not in src and "get_plan" not in src, \
        "listing clips became plan-gated — the tutorial's promise is now false"


def test_the_free_plan_is_not_quietly_given_the_paid_features():
    """The whole point of a thin free tier. These are the two that cost real
    CPU and real disk."""
    assert PLAN_LIMITS["free"]["vod"] is False
    assert PLAN_LIMITS["free"]["uploads"] is False


# ── the free tier is advertised, not merely mentioned ────────────────────────


# ── the numbers cannot be typed out any more ─────────────────────────────────
# THE DRIFT THIS FILE EXISTS FOR, CAUGHT AGAIN. The pricing block derived its
# figures from PLAN_LIMITS and was correct; the billing FAQ and the tutorial
# quickstart typed "20 clips" and "3 clips" as literals and were not covered by
# anything here. Raising the suggestion budget left both stating the old number
# with total confidence — the exact failure mode the module docstring above
# describes, in the two places nothing was looking.

import contextlib


@contextlib.contextmanager
def _plan_limit(plan, key, value):
    """Temporarily move a plan limit.

    THE ONLY WAY TO TEST DERIVATION. Asserting `str(20) in text` passes whether
    the 20 was read from PLAN_LIMITS or typed into the sentence — mutation
    testing walked straight through the first version of both tests below by
    replacing the lookup with the literal it currently equals. Moving the limit
    and watching the copy follow is what actually distinguishes them.

    PLAN_LIMITS holds ONE dict per plan, so this restores the old value rather
    than the old dict — rebinding would leave every other reader pointing at
    the original object. Same trap limits_for() documents.
    """
    d = PLAN_LIMITS[plan]
    before = d[key]
    d[key] = value
    try:
        yield
    finally:
        d[key] = before


def test_the_tutorial_quickstart_derives_its_numbers():
    import importlib
    from src.dashboard import tutorial_content as tc
    assert str(PLAN_LIMITS["free"]["max_pending"]) in tc.QUICKSTART_LEAD
    assert str(PLAN_LIMITS["free"]["max_suggested"]) in tc.QUICKSTART_LEAD

    # QUICKSTART_LEAD is built at import time, so the module has to be reloaded
    # for the moved limit to reach it. Reloaded again afterwards so the rest of
    # the suite sees the real values.
    try:
        for key, probe in (("max_pending", 4242), ("max_suggested", 3737)):
            with _plan_limit("free", key, probe):
                importlib.reload(tc)
                assert str(probe) in tc.QUICKSTART_LEAD, \
                    f"the quickstart's {key} is typed out, not read from the plan"
    finally:
        importlib.reload(tc)


def test_no_public_surface_credits_the_clippers_any_more():
    """The queue stopped saying it; these three said it too and were missed,
    because the earlier sweep only grepped the dashboard."""
    from src.dashboard.api import LANDING_HTML, _pricing
    from src.dashboard.tutorial_html import render

    def visible(html: str) -> str:
        """Rendered copy only.

        The first version of this matched a CSS comment — "overflow:hidden}
        clipped it away" — and reported the landing page as crediting viewers.
        Comments are not copy, and a test that cannot tell them apart fails on
        prose about the code rather than on the code's own prose."""
        html = re.sub(r"<!--.*?-->", "", html, flags=re.S)
        html = re.sub(r"/\*.*?\*/", "", html, flags=re.S)
        html = re.sub(r"^\s*//.*$", "", html, flags=re.M)
        return html.lower()

    banned = ("your own viewers", "viewers clipped", "clipped by viewers",
              "viewers made", "clipped it")
    for name, text in (("landing", LANDING_HTML), ("pricing", _pricing()),
                       ("tutorial", render())):
        low = visible(text)
        for claim in banned:
            assert claim not in low, f"{name} still credits the clippers: {claim!r}"

    # And the stripper has to actually strip, or this passes on an empty string.
    assert "clipped it" in visible("<p>viewers clipped it</p>")


# ── the pricing columns (landing v4, 2026-09-02) ─────────────────────────────
# Pricing is two tall columns, Starter and Pro, with Free stated above them as
# the way in. No badge, no highlighted column. Every number reads PLAN_LIMITS.

def _plan_fact(html: str, plan: str, label: str) -> str:
    """The value of one fact row in one column."""
    cols = re.findall(r'<div class="plan">(.*?)</div>(?=<div class="plan">|</div>)', html, re.S)
    col = next(c for c in cols if f'<h3 class="plan-name">{plan}</h3>' in c)
    m = re.search(r"<span>" + re.escape(label) + r"</span><b>([^<]+)</b>", col)
    assert m, f"{plan} has no {label!r} row"
    return m.group(1)


def test_free_is_stated_above_the_paid_columns():
    """A visitor scanning for "what does this cost to try" meets Free FIRST,
    with its real limits, before either priced column."""
    from src.dashboard.api import _pricing
    html = _pricing()
    lead = html[:html.index('<div class="plans">')]
    free = PLAN_LIMITS["free"]
    assert "Start free" in lead and "no card" in lead.lower()
    assert str(free["max_pending"]) in lead and str(free["max_library_week"]) in lead


def test_the_paid_columns_are_starter_then_pro():
    from src.dashboard.api import _pricing
    names = re.findall(r'<h3 class="plan-name">([^<]+)</h3>', _pricing())
    assert names == ["Starter", "Pro"], names


def test_each_column_quotes_its_real_limits():
    from src.dashboard.api import _pricing
    html = _pricing()
    for plan, key in (("Starter", "starter"), ("Pro", "pro")):
        lim = PLAN_LIMITS[key]
        assert _plan_fact(html, plan, "Clips held for review") == str(lim["max_pending"])
        assert _plan_fact(html, plan, "Highlight clips") == str(lim["max_suggested"])
        assert str(lim["max_streams"]) in _plan_fact(html, plan, "Channels at once")
        assert f"${lim['price']}" in html


def test_the_pricing_columns_derive_their_numbers():
    """`str(50) in html` passes whether the 50 was read or typed."""
    from src.dashboard.api import _pricing
    with _plan_limit("starter", "max_pending", 4242):
        assert _plan_fact(_pricing(), "Starter", "Clips held for review") == "4242"
    with _plan_limit("free", "max_pending", 3737):
        assert "3737 clips held for review" in _pricing()


def test_the_paid_columns_no_longer_say_start_free():
    """With a real free tier stated above them, a "Start free" button on a
    paid column would read as if Starter and Pro were themselves free."""
    from src.dashboard.api import _pricing
    html = _pricing()
    cols = html[html.index('<div class="plans">'):]
    assert "Start free" not in cols
    assert "Get Starter" in cols and "Get Pro" in cols


def test_one_channel_is_not_described_in_the_plural():
    from src.dashboard.api import _pricing, LANDING_HTML
    assert "1 channels" not in _pricing() and "1 channels" not in LANDING_HTML
    assert "1 channel," in _pricing()


def test_the_columns_stack_on_a_phone():
    from src.dashboard.api import LANDING_HTML
    assert re.search(r"@media\(max-width:700px\)\{\s*\.plans\{grid-template-columns:minmax\(0,1fr\)",
                     LANDING_HTML), "the two columns do not stack on a phone"
