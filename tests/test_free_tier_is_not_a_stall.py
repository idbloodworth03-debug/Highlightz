"""A free-tier account is a destination, not an incomplete purchase.

WHAT WAS WRONG. Every free user carried the note "Signed up, never opened
checkout" under their plan, and every free user was matched by the admin's
"Stalled" chip.

Both were correct once. While the free tier was closed, an account that had
signed up and not paid genuinely had nothing — the signup WAS an unfinished
journey and flagging it was the point. Reopening the free tier inverted that
without either line being revisited, so the table started describing the
product working as designed as a failure to pay, and the one list you go to
when somebody says "I paid and have no access" filled up with healthy accounts.

`signed_up` STAYS AS A STAGE NAME. Only its label and its membership of the
stalled set change. The stage is still a real, distinct funnel position, and
renaming it would break continuity with anything already recorded against it.
"""

import json
import re

from src.billing import plans


def test_the_free_tier_label_does_not_describe_it_as_a_failure_to_pay():
    label = plans.FUNNEL_LABELS["signed_up"]
    assert "checkout" not in label.lower(), \
        "the free tier is still labelled by what the user did not do"
    assert "never" not in label.lower()
    assert label == "On the free tier"


def test_a_free_signup_is_not_a_stall():
    assert "signed_up" not in plans.FUNNEL_STALLED


def test_the_real_stalls_are_still_stalls():
    """Blast radius. These two are the reason the chip exists: somebody who
    reached the card form, or for whom a Stripe customer exists with no
    subscription — the second can mean money changed hands and we missed it."""
    assert "checkout_started" in plans.FUNNEL_STALLED
    assert "checkout_dropped" in plans.FUNNEL_STALLED


def test_every_stalled_stage_is_a_real_stage():
    """A typo here would silently make the chip match nothing."""
    assert set(plans.FUNNEL_STALLED) <= set(plans.FUNNEL_STAGES)


def test_no_stalled_stage_is_one_that_has_access():
    """Staff, paying, trialing and legacy accounts are not mid-purchase, and
    listing one as stalled would send an admin chasing a customer who is fine."""
    for good in ("staff", "legacy", "trialing", "paying", "signed_up"):
        assert good not in plans.FUNNEL_STALLED, good


def test_a_plain_free_signup_still_resolves_to_the_signed_up_stage():
    """The stage itself is unchanged — this fix is about how it is described,
    not about renaming a funnel position that other records refer to."""
    assert plans.funnel_stage({"subscription_status": "none"}) == "signed_up"


def test_every_stage_still_has_a_label():
    for stage in plans.FUNNEL_STAGES:
        assert plans.FUNNEL_LABELS.get(stage), stage


# ── the two admin screens ────────────────────────────────────────────────────

def _stalled_in(html: str) -> list:
    m = re.search(r"const STALLED\s*=\s*(\[[^;]*\]);", html)
    assert m, "the STALLED list is no longer where the tests look"
    return json.loads(m.group(1))


def test_both_admin_screens_read_the_same_list():
    """These were two hand-typed copies of the same array in two script blocks,
    which is exactly how one gets updated and the other does not. Both are now
    generated from plans.FUNNEL_STALLED."""
    from src.dashboard.api import ADMIN_HTML, _ADMIN_FEEDBACK_HTML
    users = _stalled_in(ADMIN_HTML)
    growth = _stalled_in(_ADMIN_FEEDBACK_HTML)
    assert users == growth == list(plans.FUNNEL_STALLED)


def test_neither_screen_ships_an_unfilled_placeholder():
    """The fill happens after the literals close; getting that order wrong
    leaves the raw comment in the page and STALLED undefined at runtime."""
    from src.dashboard.api import ADMIN_HTML, _ADMIN_FEEDBACK_HTML
    for html in (ADMIN_HTML, _ADMIN_FEEDBACK_HTML):
        assert "<!--STALLED-->" not in html


def test_a_free_user_gets_no_second_line():
    """stageNote returns nothing for a stage that is not stalled, so the note
    disappears for free accounts without the function needing to know about
    the free tier at all."""
    from src.dashboard.api import ADMIN_HTML
    note = ADMIN_HTML[ADMIN_HTML.index("function stageNote("):]
    note = note[:note.index("\n}")]
    assert "if(!STALLED.includes(st)) return '';" in note, \
        "stageNote no longer gates on the stalled set"


def test_the_stalled_chip_uses_the_same_list():
    from src.dashboard.api import ADMIN_HTML
    assert "STALLED.includes(u.funnel_stage" in ADMIN_HTML, \
        "the Stalled chip stopped deriving from the shared list"
