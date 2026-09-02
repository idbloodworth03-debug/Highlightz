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
    section = low[pricing:low.index('id="faq"')]
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
    size = re.search(r"font-size:(\d+)px", rule)
    assert size and int(size.group(1)) >= 13, \
        f"the offer is set at {size.group(1) if size else '?'}px"


def test_the_queue_numbers_quoted_match_the_plans():
    from src.dashboard.api import LANDING_HTML
    assert f"{PLAN_LIMITS['free']['max_pending']} clips waiting on Free" in LANDING_HTML
    assert f"{PLAN_LIMITS['starter']['max_pending']} on Starter" in LANDING_HTML
    assert f"{PLAN_LIMITS['pro']['max_pending']} on Pro" in LANDING_HTML


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

def _plan_row(label: str) -> list[str]:
    """The three cells of one row of the pricing table, Free / Starter / Pro."""
    from src.dashboard.api import _pricing
    row = re.search(r'<tr[^>]*><th scope="row">' + re.escape(label) + r"</th>(.*?)</tr>",
                    _pricing(), re.S)
    assert row, f"the pricing table has no {label!r} row"
    return re.findall(r"<td>(.*?)</td>", row.group(1), re.S)


def test_free_is_a_column_in_the_pricing_table_not_a_line_of_prose():
    """THE ASK. Free existed and the page said so, but only in the paragraph
    above the plans — so a visitor scanning the pricing block for "what does
    this cost to try" saw two priced tiers reading $10 and $25 and nothing
    else. A tier nobody can see is a tier nobody signs up for. The plans are
    a spec table now, and Free is its first column."""
    from src.dashboard.api import _pricing
    html = _pricing()
    heads = re.findall(r'<th scope="col">([^<]+)</th>', html)
    assert "Free" in heads, "Free is not one of the pricing columns"


def test_the_free_column_leads_the_table():
    """It is the first rung of the same ladder — one channel, then three, then
    ten — so it reads left to right as an escalation rather than as an
    afterthought bolted on the end."""
    from src.dashboard.api import _pricing
    heads = re.findall(r'<th scope="col">([^<]+)</th>', _pricing())
    assert heads == ["Free", "Starter", "Pro"], heads


def test_the_free_price_shows_zero_and_says_why_it_is_zero():
    """"$0/month" invites the question "and then what?". The line under the
    figure answers it in the one place a visitor is definitely looking."""
    free_price = _plan_row("Price")[0]
    assert "$0" in free_price
    assert "no card" in free_price.lower(), \
        "the free price does not say a card is not needed"


def test_the_free_column_quotes_its_real_limits():
    free = PLAN_LIMITS["free"]
    assert _plan_row("Channels at once")[0] == str(free["max_streams"])
    assert _plan_row("Clips held for review")[0] == str(free["max_pending"])
    assert _plan_row("Highlight clips")[0] == str(free["max_suggested"])


def test_the_paid_columns_no_longer_say_start_free():
    """They said "Start free" when every signup was a trial of the full
    product. With a real free tier next to them that reads as if Starter and
    Pro are themselves free, which is the one misreading this table cannot
    afford."""
    from src.dashboard.api import _pricing
    foot = re.search(r"<tfoot>(.*?)</tfoot>", _pricing(), re.S).group(1)
    cells = re.findall(r"<td>(.*?)</td>", foot, re.S)
    assert len(cells) == 4, "the action row is not one cell per column"
    assert "Start free" in cells[1] and "Start free" not in cells[2] + cells[3], \
        "a paid column still offers to start free"
    assert "Get Starter" in cells[2] and "Get Pro" in cells[3]


def test_one_channel_is_not_described_in_the_plural():
    """The channels cell is written "N channels watched at the same time",
    which reads as "1 channels" on the only plan where N is 1 — in the one
    sentence on the page that quotes all three."""
    from src.dashboard.api import _channels_fact, LANDING_HTML
    _, sentence = _channels_fact()
    assert "1 channels" not in sentence
    assert "1 channel</b>" in sentence
    assert "1 channels" not in LANDING_HTML


def test_the_table_stays_a_table_on_a_phone():
    """The three cards used to stack; a spec table must NOT — a plan per
    screen is the thing a comparison table exists to avoid. So the layout is
    fixed (four columns share the width whatever their content) and the label
    column widens on a phone so the row names still read."""
    from src.dashboard.api import LANDING_HTML
    table = re.search(r"\n  \.plans\{([^}]*)\}", LANDING_HTML).group(1)
    assert "table-layout:fixed" in table, "the columns can be squeezed by their content"
    assert "width:100%" in table
    m = re.search(r"@media\(max-width:600px\)\{[^@]*\.plans col\.c-lab\{width:(\d+)%\}",
                  LANDING_HTML)
    assert m, "no phone rule for the label column"
    desk = int(re.search(r"\.plans col\.c-lab\{width:(\d+)%\}", LANDING_HTML).group(1))
    assert int(m.group(1)) > desk, "the label column does not widen on a phone"


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


def test_the_billing_faq_derives_every_number_it_quotes():
    from src.dashboard.api import LANDING_HTML, _free_plan_answer
    ans = _free_plan_answer()
    f, st, pro = (PLAN_LIMITS["free"], PLAN_LIMITS["starter"], PLAN_LIMITS["pro"])
    for n in (f["max_streams"], f["max_pending"], f["max_suggested"],
              st["price"], st["max_streams"], st["max_pending"],
              pro["price"], pro["max_streams"], pro["max_pending"]):
        assert str(n) in ans, f"the FAQ no longer quotes {n}"
    assert ans in LANDING_HTML, "the generated answer never reached the page"
    assert "<!--FREEPLAN-->" not in LANDING_HTML, "the placeholder was left unfilled"

    # And it really is reading them, not restating today's values.
    for key, probe in (("max_pending", 4242), ("max_suggested", 3737),
                       ("max_streams", 8181)):
        with _plan_limit("free", key, probe):
            assert str(probe) in _free_plan_answer(), \
                f"the FAQ's {key} is typed out, not read from the plan"

    # Deriving the number while hardcoding its noun only moves the staleness.
    # Free watches one channel today, so the sentence reads "1 channel"; raise
    # the limit and it has to become "channels" or the page is ungrammatical
    # in exactly the way deriving was supposed to prevent.
    assert "1 channel at a time" in _free_plan_answer()
    with _plan_limit("free", "max_streams", 2):
        ans = _free_plan_answer()
        assert "2 channels at a time" in ans, "the FAQ does not pluralise channels"
        assert "2 channel at a time" not in ans


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
    from src.dashboard.api import LANDING_HTML, _pricing, _free_plan_answer
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
                       ("billing FAQ", _free_plan_answer()), ("tutorial", render())):
        low = visible(text)
        for claim in banned:
            assert claim not in low, f"{name} still credits the clippers: {claim!r}"

    # And the stripper has to actually strip, or this passes on an empty string.
    assert "clipped it" in visible("<p>viewers clipped it</p>")
