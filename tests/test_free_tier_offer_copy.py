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
    assert rows["Crowd suggestions"][1] == str(PLAN_LIMITS["free"]["max_suggested"])
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
