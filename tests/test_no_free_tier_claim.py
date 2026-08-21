"""The public pages must not advertise a tier nobody can sign up for.

WHAT WENT WRONG. plans.py marks the free tier LEGACY ONLY — "Nothing new ever
lands here." New signups get 7 days of the full product, then `locked` (zero
streams, zero queue) unless they pay. But the landing page still sold a free
tier as one of three choices:

    "1 channel free, 3 on Starter, 10 on Pro"
    "15 pending free, 50 on Starter, 200 on Pro"

and the tutorial said, flatly:

    "Highlightz is free to start and stays free"
    "Cancelling ... You drop to Free and keep your library"

Neither is true for anyone who signed up after the trial cutover. The second
is worse than marketing drift: it tells a cancelling customer they will land
somewhere they will not, and what they actually land on is a locked account.

THE DURABLE FIX IS THE ASSERTION, NOT THE EDIT. The copy drifted from the code
because nothing tied them together. These derive the numbers from PLAN_LIMITS,
so changing a plan breaks the test rather than quietly making the page lie.

A trial IS free, so "7 days free", "free trial" and "free with no card" are all
fine and deliberately not matched. What is banned is `free` presented as a
standing TIER alongside Starter and Pro.
"""

import re

import pytest

from src.billing.plans import PLAN_LIMITS


def _public_copy() -> dict:
    from src.dashboard.api import LANDING_HTML
    from src.dashboard import tutorial_content
    from src.dashboard.tutorial_html import render
    return {
        "landing": LANDING_HTML,
        "tutorial": render(),
    }


# ── the specific claims that were wrong ──────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "1 channel free",
    "15 pending free",
    "stays free",
    "drop to Free",
])
def test_the_exact_false_claims_are_gone(phrase):
    for name, text in _public_copy().items():
        assert phrase not in text, f"{name} still claims: {phrase!r}"


def test_no_page_offers_free_as_a_tier_beside_the_paid_ones():
    """The shape of the bug: `free` listed as one option in a run of tiers.
    Matches "<something> free, <n> on Starter" and friends."""
    bad = re.compile(r"free,\s*\d+\s+on\s+(Starter|Pro)", re.I)
    for name, text in _public_copy().items():
        hit = bad.search(text)
        assert hit is None, f"{name} lists free as a tier: {hit.group(0)!r}"


def test_no_page_says_the_product_is_free_after_the_trial():
    for name, text in _public_copy().items():
        low = text.lower()
        for claim in ("free forever", "always free", "free plan", "free tier"):
            assert claim not in low, f"{name} advertises {claim!r}"


# ── what the pages DO say has to match the code ──────────────────────────────

def test_the_channel_numbers_quoted_match_the_plans():
    """Starter 3 and Pro 10 are stated in prose all over the page. If the plan
    table changes and the copy does not, the page starts overselling."""
    from src.dashboard.api import LANDING_HTML
    assert f"{PLAN_LIMITS['starter']['max_streams']} on Starter" in LANDING_HTML
    assert f"{PLAN_LIMITS['pro']['max_streams']} on Pro" in LANDING_HTML


def test_the_queue_numbers_quoted_match_the_plans():
    from src.dashboard.api import LANDING_HTML
    assert f"{PLAN_LIMITS['starter']['max_pending']} on Starter" in LANDING_HTML
    assert f"{PLAN_LIMITS['pro']['max_pending']} on Pro" in LANDING_HTML


def test_the_trial_is_described_as_the_full_product():
    """It resolves to pro, so the page may say so — and should, because it is
    the strongest true thing about the offer."""
    from src.billing.plans import get_plan
    trial_user = {"id": "t", "subscription_status": "trialing"}
    assert get_plan(trial_user) == "pro"
    from src.dashboard.api import LANDING_HTML
    assert f"{PLAN_LIMITS['pro']['max_streams']} during your trial" in LANDING_HTML


def test_the_trial_length_is_not_hardcoded_wrong():
    from src.billing.plans import TRIAL_DAYS
    from src.dashboard.api import LANDING_HTML
    assert TRIAL_DAYS == 7
    assert f"{TRIAL_DAYS} days free" in LANDING_HTML


# ── the cancellation claim ───────────────────────────────────────────────────

def test_a_cancelling_new_user_really_does_land_on_locked():
    """The fact behind the tutorial edit. Only a grandfathered account drops to
    free; everyone else is locked, with zero streams and zero queue."""
    from src.billing.plans import get_plan, limits_for
    cancelled_new = {"id": "n", "subscription_status": "canceled"}
    assert get_plan(cancelled_new) == "locked"
    assert limits_for(cancelled_new)["max_streams"] == 0
    assert limits_for(cancelled_new)["max_pending"] == 0

    grandfathered = {"id": "g", "subscription_status": "canceled",
                     "grandfathered": True}
    assert get_plan(grandfathered) == "free"


def test_the_tutorial_does_not_promise_a_soft_landing():
    """"You drop to Free and keep your library" was the claim. Half of it was
    false and the true half is worth keeping, so the replacement says only the
    part that holds: the clips stay."""
    from src.dashboard.tutorial_html import render
    t = render()
    assert "stays in your library" in t
    assert "drop to Free" not in t


def test_clips_really_do_survive_a_lapse():
    """Checks the surviving half is true rather than assumed. GET /clips has no
    plan gate, so a locked user can still read their library."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.list_clips)
    assert "limits_for" not in src and "get_plan" not in src, \
        "listing clips became plan-gated — the tutorial's promise is now false"


# ── free is still real for the people who have it ────────────────────────────

def test_the_free_plan_still_exists_for_grandfathered_accounts():
    """Removing the CLAIM must not remove the TIER. Legacy users still hold it
    and their limits must not move."""
    assert PLAN_LIMITS["free"]["max_streams"] == 1
    assert PLAN_LIMITS["free"]["max_pending"] == 15


# ── the offer, said the same way everywhere ──────────────────────────────────
# ASKED FOR DIRECTLY: "7 days free with no credit card required, make sure it
# says that on it." It already did — in five different phrasings. The hero said
# "no credit card", pricing said "no credit card.", the FAQ said "Seven days
# free with no credit card", the closing line said "no card", and the sign-in
# badge said "no card required". Five ways of saying one thing reads as five
# slightly different offers.
#
# These pin the exact wording on every page a visitor can reach before paying,
# and ban the short forms so it cannot drift back.

_OFFER = "no credit card required"


def _public_pages() -> dict:
    from src.dashboard.api import LANDING_HTML, LOGIN_HTML
    from src.dashboard.tutorial_html import render
    return {"landing": LANDING_HTML, "tutorial": render(), "login": LOGIN_HTML}


def _visible(html: str) -> str:
    """Copy a visitor can actually read. Stylesheets and comments are not copy,
    and searching them for short forms matched the CSS comment
    "/* FAQ. Hairline rows, no card. */" as if it were an offer."""
    out = re.sub(r"<style.*?</style>", "", html, flags=re.S)
    out = re.sub(r"<script.*?</script>", "", out, flags=re.S)
    return re.sub(r"<!--.*?-->", "", out, flags=re.S)


def test_every_public_page_states_the_trial_the_same_way():
    for name, html in _public_pages().items():
        assert _OFFER in html, f"{name} does not say {_OFFER!r}"


def test_the_trial_length_is_stated_as_a_numeral_everywhere():
    """"Seven days free" and "7 days free" on the same page is the same drift in
    a different coat."""
    from src.billing.plans import TRIAL_DAYS
    for name, html in _public_pages().items():
        assert f"{TRIAL_DAYS} days free" in html, f"{name} does not say the trial length"
        assert "Seven days free" not in html, f"{name} spells the number out"


@pytest.mark.parametrize("short", ["no card required", "no card.", "no credit card."])
def test_the_short_forms_stay_gone(short):
    """Each of these was live on a different page."""
    for name, html in _public_pages().items():
        assert short not in _visible(html), \
            f"{name} is back to the short form: {short!r}"


def test_the_hero_states_it_above_the_fold():
    """The one place it has to be, and it was the dimmest text on the page."""
    from src.dashboard.api import LANDING_HTML
    hero = LANDING_HTML[LANDING_HTML.index("<header class=\"wrap hero"):]
    hero = hero[:hero.index("</header>")]
    assert "7 days free" in hero and _OFFER in hero, \
        "the trial offer is no longer in the hero"


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
    assert "var(--ink-3)" not in base, "the trial offer is back in the muted ink"
    size = re.search(r"font-size:([\d.]+)px", base)
    assert size and float(size.group(1)) >= 13, \
        f"the trial offer is set at {size.group(1) if size else '?'}px"

    # And it must stay readable where most people will see it.
    phone = re.search(r"@media\(max-width:700px\)\{.*?\.hero-note\{font-size:([\d.]+)px\}",
                      LANDING_HTML, re.S)
    if phone:
        assert float(phone.group(1)) >= 12, \
            f"the offer drops to {phone.group(1)}px on a phone"
