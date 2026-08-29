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


def test_the_hero_states_it_above_the_fold():
    """The one place it has to be. Burying the terms below the fold is how a
    signup becomes a surprise — which was true when the surprise was a card
    form and is true now that the good news is there isn't one."""
    from src.dashboard.api import LANDING_HTML
    hero = LANDING_HTML[LANDING_HTML.index('<header class="wrap hero'):]
    hero = hero[:hero.index("</header>")]
    low = hero.lower()
    assert "free to start" in low, "the offer is no longer in the hero"
    assert _OFFER in low, "the hero does not mention the card"
    assert _ESCAPE in low, "the hero does not say it is not a trial"


def test_the_offer_is_not_set_in_the_faintest_ink_on_the_page():
    """It is the strongest true thing about the offer. It was 12px of --ink-3,
    the dimmest step in the palette, which is where you put a footnote."""
    from src.dashboard.api import LANDING_HTML
    # There are three .hero-note rules: a width override inside a media query,
    # the real one, and a phone size. Pick the one that actually sets the type,
    # or this reads the first match and asserts nothing.
    rules = [m.group(1) for m in re.finditer(r"\.hero-note\{([^}]*)\}", LANDING_HTML)
             if "font-family" in m.group(1)]
    assert len(rules) == 1, f"expected one type rule for .hero-note, found {len(rules)}"
    base = rules[0]
    assert "var(--ink-3)" not in base, "the offer is back in the muted ink"
    size = re.search(r"font-size:([\d.]+)px", base)
    assert size and float(size.group(1)) >= 13, \
        f"the offer is set at {size.group(1) if size else '?'}px"

    phone = re.search(r"@media\(max-width:700px\)\{.*?\.hero-note\{font-size:([\d.]+)px\}",
                      LANDING_HTML, re.S)
    if phone:
        assert float(phone.group(1)) >= 12, \
            f"the offer drops to {phone.group(1)}px on a phone"


# ── what the pages say has to match the code ─────────────────────────────────

def test_the_channel_numbers_quoted_match_the_plans():
    """Free 1, Starter 3 and Pro 10 are stated in prose. If the plan table
    changes and the copy does not, the page starts overselling."""
    from src.dashboard.api import LANDING_HTML
    assert f"{PLAN_LIMITS['free']['max_streams']} on Free" in LANDING_HTML
    assert f"{PLAN_LIMITS['starter']['max_streams']} on Starter" in LANDING_HTML
    assert f"{PLAN_LIMITS['pro']['max_streams']} on Pro" in LANDING_HTML


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

def test_free_is_a_card_in_the_pricing_row_not_a_line_of_prose():
    """THE ASK. Free existed and the page said so, but only in the paragraph
    above the row — so a visitor scanning the pricing block for "what does this
    cost to try" saw two priced cards reading $10 and $25 and nothing else.
    A tier nobody can see is a tier nobody signs up for."""
    from src.dashboard.api import _pricing
    html = _pricing()
    assert html.count('<div class="ptier ') == 3, \
        "the pricing row is not three cards"
    assert re.search(r'<span class="ptier-name">Free</span>', html), \
        "Free is not one of the pricing cards"


def test_the_free_card_leads_the_row():
    """It is the first rung of the same ladder — one channel, then three, then
    ten — so it reads left to right as an escalation rather than as an
    afterthought bolted on the end."""
    from src.dashboard.api import _pricing
    names = re.findall(r'<span class="ptier-name">([^<]+)</span>', _pricing())
    assert names == ["Free", "Starter", "Pro"], names


def test_the_free_card_shows_zero_and_says_why_it_is_zero():
    """"$0/month" invites the question "and then what?". The suffix answers it
    in the one place a visitor is definitely looking."""
    from src.dashboard.api import _pricing
    card = _pricing().split('<div class="ptier ptier-a">')[1] \
                     .split('<div class="ptier ptier-b">')[0]
    assert "$0" in card
    assert "no card" in card.lower(), \
        "the free card's price does not say a card is not needed"


def test_the_free_card_quotes_its_real_limits():
    from src.dashboard.api import _pricing
    free_card = _pricing().split('<div class="ptier ptier-a">')[1] \
                          .split('<div class="ptier ptier-b">')[0]
    free = PLAN_LIMITS["free"]
    assert str(free["max_streams"]) in free_card
    assert str(free["max_pending"]) in free_card
    assert str(free["max_suggested"]) in free_card


def test_the_paid_cards_no_longer_say_start_free():
    """They said "Start free" when every signup was a trial of the full
    product. With a real free tier next to them that reads as if Starter and
    Pro are themselves free, which is the one misreading this row cannot
    afford."""
    from src.dashboard.api import _pricing
    paid = _pricing().split('<div class="ptier ptier-b">')[1]
    assert "Start free" not in paid, "a paid card still offers to start free"
    assert "Get Starter" in paid and "Get Pro" in paid


def test_one_channel_is_not_described_in_the_plural():
    """The card builder writes "N channels watched at the same time", which
    reads as "1 channels" on the only plan where N is 1 — on the card most new
    visitors read first."""
    from src.dashboard.api import _pricing
    assert "1 channels" not in _pricing()
    assert "1 channel</b>" in _pricing()


def test_the_row_still_collapses_on_a_phone():
    """Three columns need to break earlier than two did. Measured before this:
    at 760px the middle card was 210px wide and its price wrapped under its own
    name."""
    from src.dashboard.api import LANDING_HTML

    def columns_at(width):
        """The column count the row resolves to at a breakpoint.

        COUNTED, not pattern-matched. The first version of this asserted the
        980 rule matched "minmax(0,1fr) minmax(0,1fr)" — which is a PREFIX of
        the three-column value, so a mutation putting three columns back at 980
        sailed through it. Counting is the only version that cannot be
        satisfied by a wider rule that happens to start the same way."""
        m = re.search(r"@media \(max-width:" + str(width)
                      + r"px\)\{\s*\.ptiers\{grid-template-columns:([^;}]*)",
                      LANDING_HTML)
        assert m, f"no .ptiers rule at max-width:{width}px"
        return m.group(1).count("minmax(")

    assert columns_at(980) == 2, "the three-column row has no two-column step"
    assert columns_at(700) == 1, "the pricing row does not go single-column on a phone"


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
