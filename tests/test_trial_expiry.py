"""A finished trial must read as finished everywhere, not just to the user.

THE REPORTED BUG. The admin table showed "Trial ends <a date already past>".

THE MECHANISM. The decision "this trial is over" lived in exactly one place:
the dashboard auth middleware, which runs against the REQUESTING user. So it
only ever fired when the trialing user themselves made a request — and somebody
whose trial ran out and never came back is precisely the person that never
happens for. Their stored `subscription_status` stayed "trialing" indefinitely,
so every reader that does not run on their request saw a live trial:

  * the admin row rendered "Trial ends" in the future tense against a past date;
  * plans.get_plan resolved them to "pro";
  * plans.funnel_stage counted them among people still converting;
  * userState() put "expired" in the same bucket as a brand-new signup, so a
    finished trial appeared under Active and never under Lapsed.

THE TRAP THIS FIX HAD TO AVOID is documented at length in
plans.trial_expired: `trial_ends_at` is 0 for "we do not know when this ends",
and a bare `ends_at < now` reads 0 as 1970 and expires every such trial. A
card-up-front Stripe trial whose webhook has not landed has exactly that shape,
so the naive version revokes a customer at the instant they pay. Several tests
below exist only to hold that line.
"""

import re
import time

import pytest

from src.billing import plans

DAY = 86400


def trialing(ends_in_days=None, **kw):
    ends = 0 if ends_in_days is None else time.time() + ends_in_days * DAY
    return dict({"id": "u1", "subscription_status": "trialing",
                 "trial_ends_at": ends}, **kw)


# ── the shared decision ──────────────────────────────────────────────────────

def test_a_trial_past_its_end_date_is_expired():
    assert plans.trial_expired(trialing(ends_in_days=-5)) is True


def test_a_trial_still_running_is_not():
    assert plans.trial_expired(trialing(ends_in_days=3)) is False


def test_a_trial_with_no_end_date_is_never_expired():
    """THE LOAD-BEARING CASE. 0 means "we do not know when this ends", not
    "ended in 1970". A card-up-front Stripe trial whose webhook has not landed
    yet, or landed without a trial_end, has exactly this shape — expiring it
    would revoke a paying customer at the moment they paid."""
    assert plans.trial_expired(trialing(ends_in_days=None)) is False
    assert plans.trial_expired({"subscription_status": "trialing",
                                "trial_ends_at": 0}) is False
    assert plans.trial_expired({"subscription_status": "trialing"}) is False


def test_a_negative_or_junk_end_date_is_treated_as_unknown_not_as_expired():
    """Same failing-open rule, one step further out: any non-positive value
    means we do not know, and the safe reading of "do not know" is "running"."""
    assert plans.trial_expired({"subscription_status": "trialing",
                                "trial_ends_at": -1}) is False
    assert plans.trial_expired({"subscription_status": "trialing",
                                "trial_ends_at": None}) is False


def test_it_only_speaks_about_trials():
    for status in ("active", "canceled", "inactive", "past_due", "expired", "none"):
        assert plans.trial_expired({"subscription_status": status,
                                    "trial_ends_at": time.time() - DAY}) is False, status
    assert plans.trial_expired(None) is False
    assert plans.trial_expired({}) is False


def test_the_boundary_is_inclusive():
    """Matches the middleware's original `time.time() >= trial_ends_at`, so
    moving the condition into one place changed no verdict."""
    now = 1_000_000.0
    u = {"subscription_status": "trialing", "trial_ends_at": now}
    assert plans.trial_expired(u, now=now) is True
    assert plans.trial_expired(u, now=now - 0.001) is False


# ── what the readers now say ─────────────────────────────────────────────────

def test_an_expired_trial_resolves_to_free_without_the_user_coming_back():
    """This is the substantive half. Before, the answer was "pro" until they
    next made a request — so background limit lookups handed a finished trial
    Pro caps indefinitely."""
    assert plans.get_plan(trialing(ends_in_days=-1)) == "free"


def test_a_live_trial_still_resolves_to_pro():
    """Blast radius: the fix must not cut a running trial short."""
    assert plans.get_plan(trialing(ends_in_days=2)) == "pro"


def test_a_trial_with_no_end_date_still_resolves_to_pro():
    assert plans.get_plan(trialing(ends_in_days=None)) == "pro"


def test_a_comped_tier_on_a_live_trial_is_still_honoured():
    assert plans.get_plan(trialing(ends_in_days=2, plan="starter")) == "starter"


def test_staff_are_unaffected_by_a_stale_trial_date():
    """Admins and trainers are comped by a different field entirely; an old
    trial_ends_at lying around on their record must not demote them."""
    assert plans.get_plan(trialing(ends_in_days=-30, is_admin=True)) == "pro"
    assert plans.get_plan(trialing(ends_in_days=-30, is_labeler=True)) == "pro"


def test_paying_customers_are_untouched():
    assert plans.get_plan({"subscription_status": "active", "plan": "starter"}) == "starter"
    assert plans.get_plan({"subscription_status": "active", "plan": "pro"}) == "pro"


def test_the_funnel_stops_counting_a_finished_trial_as_converting():
    assert plans.funnel_stage(trialing(ends_in_days=2)) == "trialing"
    assert plans.funnel_stage(trialing(ends_in_days=None)) == "trialing"
    assert plans.funnel_stage(
        trialing(ends_in_days=-2, stripe_customer_id="cus_1")) == "lapsed"


# ── one source of truth ──────────────────────────────────────────────────────

def test_the_middleware_uses_the_shared_helper():
    """Two copies of this condition is how the bug happened. The middleware
    must not carry its own."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api)
    assert "_plans.trial_expired(db_user)" in src, \
        "the access gate no longer uses the shared decision"
    assert not re.search(r'status == "trialing" and trial_ends_at > 0', src), \
        "the middleware grew its own copy of the expiry condition again"


def test_the_admin_list_sends_the_effective_status():
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.admin_list_users)
    assert "_plans.trial_expired(u)" in src, \
        "the admin table is back to reading the raw stored status"


def test_the_admin_list_does_not_write_back():
    """A GET must not mutate. The middleware persists the transition when the
    user returns; doing it here would also owe a broadcast per the realtime
    contract, for a change no open tab of theirs is watching."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.admin_list_users)
    assert "update_subscription" not in src


# ── the row the user was looking at ──────────────────────────────────────────

def _admin_js() -> str:
    from src.dashboard.api import ADMIN_HTML
    return ADMIN_HTML


def test_the_row_never_says_a_past_date_is_upcoming():
    js = _admin_js()
    note = js[js.index("function planNote("):]
    note = note[:note.index("\n}")]
    assert "Trial ended " in note, "there is no past-tense label"
    assert "u.trial_ends_at * 1000 <= Date.now()" in note, \
        "the label does not compare the end date against now"


def test_a_finished_trial_is_styled_as_lapsed_not_as_a_live_trial():
    js = _admin_js()
    note = js[js.index("function planNote("):]
    note = note[:note.index("\n}")]
    ended = note[note.index("Trial ended "):]
    assert "'lapsed'" in ended[:120], "a finished trial still carries the trial pill"


def test_an_expired_user_lands_in_lapsed_not_in_active():
    """`expired` used to fall through to 'none' — the bucket for a brand-new
    signup — so a finished trial showed under the default Active filter and
    never under Lapsed."""
    js = _admin_js()
    state = js[js.index("function userState("):]
    state = state[:state.index("\n}")]
    assert "st === 'expired'" in state, "userState does not recognise 'expired'"
    assert re.search(r"st === 'expired'\) return 'lapsed'", state), \
        "an expired trial is not routed to the lapsed bucket"


def test_the_active_filter_still_includes_the_free_tier():
    """Blast radius on the filter. Free signups carry 'none' and must stay in
    the default view — that was a deliberate fix earlier and this must not
    quietly undo it."""
    js = _admin_js()
    m = re.search(r"U_FILTER === 'active'\) return ([^;]+);", js)
    assert m, "the active filter changed shape"
    assert "st === 'none'" in m.group(1)
    assert "st === 'active'" in m.group(1)
    assert "st === 'trialing'" in m.group(1)
    assert "st === 'expired'" not in m.group(1), \
        "a finished trial is being counted as an active user"
