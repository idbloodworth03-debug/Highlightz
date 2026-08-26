"""
Tests for the Stripe billing mapping that drives the trial→paid transition.

sync_subscription_event is the single point that interprets Stripe webhooks into
our (customer_id, user_id, status) model. The webhook handler then links the
customer and flips the user's subscription_status, so getting this mapping right
is what makes a trial convert to paid (and a lapse revoke access).
"""

import asyncio
from unittest.mock import MagicMock, patch

from src.billing import stripe_billing as sb


def _sub_event(etype: str, status: str, *, cust="cus_123", user="u_42"):
    return {
        "id": "evt_1",
        "type": etype,
        "data": {"object": {
            "customer": cust,
            "status": status,
            "metadata": {"user_id": user},
        }},
    }


def test_subscription_created_active_maps_to_active():
    # The trial→paid event: checkout creates the subscription, Stripe fires
    # customer.subscription.created with status=active and our user_id in metadata.
    cust, user, status = sb.sync_subscription_event(
        _sub_event("customer.subscription.created", "active"))
    assert (cust, user, status) == ("cus_123", "u_42", "active")


def test_trialing_is_kept_distinct_from_active_and_still_grants_access():
    """Trialing used to be folded into `active` here, which was free while no
    Stripe subscription could ever be trialing — the 7 free days were
    app-managed. Card-up-front signups make it the opening state of a real
    subscription, and "we have their card, first charge on day 7" is not the
    same fact as "we have charged them". Both still grant full access, which is
    what this test has always actually been protecting."""
    from src.billing import plans
    cust, user, status = sb.sync_subscription_event(
        _sub_event("customer.subscription.updated", "trialing"))
    assert (cust, user, status) == ("cus_123", "u_42", "trialing")
    assert status in plans.ACTIVE_STATUSES
    assert plans.get_plan({"subscription_status": status, "plan": "starter"}) == "starter"


def test_a_stripe_trial_carries_its_end_date_through():
    """The dashboard counts down to this. It has to be Stripe's date — the day
    the card is charged — not a second clock of our own."""
    ev = _sub_event("customer.subscription.created", "trialing")
    ev["data"]["object"]["trial_end"] = 1893456000
    assert sb.extract_trial_end(ev) == 1893456000


def test_a_missing_or_junk_trial_end_is_zero_not_a_crash():
    ev = _sub_event("customer.subscription.created", "trialing")
    assert sb.extract_trial_end(ev) == 0
    ev["data"]["object"]["trial_end"] = "not-a-date"
    assert sb.extract_trial_end(ev) == 0


def test_canceled_maps_to_inactive():
    _, _, status = sb.sync_subscription_event(
        _sub_event("customer.subscription.deleted", "canceled"))
    assert status == "inactive"


def test_unpaid_and_incomplete_expired_map_to_inactive():
    for raw in ("unpaid", "incomplete_expired"):
        _, _, status = sb.sync_subscription_event(
            _sub_event("customer.subscription.updated", raw))
        assert status == "inactive", raw


def test_past_due_passes_through_for_gating():
    # The raw status is preserved rather than collapsed to inactive, which is
    # what lets the grace handling in plans.GRACE_STATUSES tell "the card is
    # being retried" apart from "they cancelled". (It no longer means
    # no-access, as the original version of this comment said — see
    # test_billing_grace.py for what past_due now grants.)
    _, _, status = sb.sync_subscription_event(
        _sub_event("customer.subscription.updated", "past_due"))
    assert status == "past_due"


def test_non_subscription_event_is_ignored():
    # checkout.session.completed and friends are not subscription lifecycle events;
    # the handler must no-op on them (returns empties → no status write).
    cust, user, status = sb.sync_subscription_event({
        "id": "evt_2", "type": "checkout.session.completed",
        "data": {"object": {"customer": "cus_x"}},
    })
    assert (cust, user, status) == (None, None, "")


def test_checkout_enables_promotion_codes():
    # The 50%-off-first-month promo is a Stripe Coupon + Promotion Code; checkout
    # must opt in to the code box or users can never enter it. Lock the flag so a
    # future edit can't silently drop it.
    fake_client = MagicMock()
    fake_client.checkout.sessions.create.return_value = MagicMock(url="https://checkout")
    with patch.object(sb, "_client", return_value=fake_client):
        url = asyncio.run(sb.create_checkout_url("u_1", "alice", "price_pro"))
    assert url == "https://checkout"
    params = fake_client.checkout.sessions.create.call_args.kwargs["params"]
    assert params["allow_promotion_codes"] is True
    # The subscription must still carry user_id so the webhook can attribute it.
    assert params["subscription_data"]["metadata"]["user_id"] == "u_1"


def _checkout_params(price_id="price_pro", trial_days=0):
    fake_client = MagicMock()
    fake_client.checkout.sessions.create.return_value = MagicMock(url="https://checkout")
    with patch.object(sb, "_client", return_value=fake_client):
        asyncio.run(sb.create_checkout_url("u_1", "alice", price_id,
                                           trial_days=trial_days))
    return fake_client.checkout.sessions.create.call_args.kwargs["params"]


def test_checkout_charges_immediately_when_no_free_days_are_offered():
    # The returning-subscriber path, and anyone who has already used their one
    # free week. Checkout must not quietly attach a trial to them.
    p = _checkout_params()
    assert "trial_period_days" not in p["subscription_data"]
    assert p["mode"] == "subscription"
    assert p["payment_method_types"] == ["card"]
    # Checkout charges exactly the tier price it was asked for.
    assert p["line_items"] == [{"price": "price_pro", "quantity": 1}]


def test_checkout_attaches_free_days_only_when_a_caller_asks_for_them():
    """The self-serve trial is retired, so nothing in the product passes a
    non-zero trial_days any more (see _checkout_trial_days). The CAPABILITY
    stays and stays tested: reinstating a trial should be a one-line decision
    at the call site, not a rediscovery of how Stripe wants it expressed."""
    p = _checkout_params(trial_days=7)
    assert p["subscription_data"]["trial_period_days"] == 7
    # Still the real price — a trial delays the charge, it does not discount it.
    assert p["line_items"] == [{"price": "price_pro", "quantity": 1}]

    # And the default really is no trial, which is what ships today.
    assert "trial_period_days" not in _checkout_params()["subscription_data"]


def test_a_card_is_collected_on_a_trial_checkout_too():
    """THE POINT OF THE WHOLE CUTOVER. Stripe's default for a trialing
    subscription in Checkout is payment_method_collection='if_required', which
    creates the trial with NO card when nothing is due today — silently
    restoring the exact thing this change exists to end, and only on trial
    checkouts, which is the hardest kind of bug to notice."""
    for days in (0, 7):
        p = _checkout_params(trial_days=days)
        assert p["payment_method_collection"] == "always", \
            f"no card is collected when trial_days={days}"


def test_checkout_reuses_existing_stripe_customer():
    # A re-subscribe must land on the SAME Stripe customer — a fresh customer
    # per checkout orphans the old subscription (invisible, unkillable billing).
    fake_client = MagicMock()
    fake_client.checkout.sessions.create.return_value = MagicMock(url="https://checkout")
    with patch.object(sb, "_client", return_value=fake_client):
        asyncio.run(sb.create_checkout_url("u_1", "alice", "price_pro", customer_id="cus_A"))
    params = fake_client.checkout.sessions.create.call_args.kwargs["params"]
    assert params["customer"] == "cus_A"
    # And without a known customer, Stripe creates one (param absent).
    fake_client.reset_mock()
    with patch.object(sb, "_client", return_value=fake_client):
        asyncio.run(sb.create_checkout_url("u_1", "alice", "price_pro"))
    assert "customer" not in fake_client.checkout.sessions.create.call_args.kwargs["params"]


def test_live_subscription_status_prefers_active_and_fails_open(monkeypatch):
    monkeypatch.setattr(sb.settings, "stripe_secret_key", "sk_test")
    fake_client = MagicMock()
    fake_client.subscriptions.list.return_value = MagicMock(
        data=[{"status": "canceled"}, {"status": "past_due"}, {"status": "active"}])
    with patch.object(sb, "_client", return_value=fake_client):
        assert asyncio.run(sb.live_subscription_status("cus_A")) == "active"
    fake_client.subscriptions.list.return_value = MagicMock(data=[{"status": "canceled"}])
    with patch.object(sb, "_client", return_value=fake_client):
        assert asyncio.run(sb.live_subscription_status("cus_A")) is None
    # Errors must fail OPEN (None) — a Stripe hiccup must never block checkout.
    with patch.object(sb, "_client", side_effect=RuntimeError("boom")):
        assert asyncio.run(sb.live_subscription_status("cus_A")) is None
    assert asyncio.run(sb.live_subscription_status("")) is None


def test_apply_subscription_event_mismatch_targets_customer_owner(monkeypatch):
    # Stale-customer cancellation: metadata names user X, but X's stored customer
    # is B != A. The event must NOT touch X (their B-subscription is healthy);
    # it applies by customer and affects whoever owns A — here, nobody.
    from src.dashboard import api
    from src.auth import users as user_store
    calls = {}
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "stripe_customer_id": "cus_B"})
    monkeypatch.setattr(user_store, "update_subscription",
                        lambda *a: calls.setdefault("by_user", []).append(a))
    monkeypatch.setattr(user_store, "update_subscription_by_customer",
                        lambda *a: calls.setdefault("by_cust", []).append(a) or None)
    affected = api.apply_subscription_event("user_X", "cus_A", "inactive")
    assert affected is None                    # nobody owns cus_A anymore
    assert "by_user" not in calls              # user_X untouched
    # None for the trial end: an event that is not about a trial must not
    # overwrite a trial end date that is.
    assert calls["by_cust"] == [("cus_A", "inactive", None)]
    # Matching customer → normal per-user update, affected user returned.
    calls.clear()
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "stripe_customer_id": "cus_A"})
    assert api.apply_subscription_event("user_X", "cus_A", "active") == "user_X"
    assert calls["by_user"] == [("user_X", "cus_A", "active", None)]


def test_no_paywall_variant_promises_free_days_that_do_not_exist():
    """The rule this test has always protected: never dangle free days in front
    of somebody who cannot get them. With the self-serve trial retired that is
    EVERYBODY, so it applies to every variant instead of two of them.

    The variants also must not imply access has stopped. Nobody is locked out
    any more — they are reading this from a working free account — and telling
    them their access ended is the same class of lie in the other direction.
    """
    from src.dashboard.api import _paywall_copy
    variants = {k: _paywall_copy(k) for k in ("new", "returning", "trial_ended")}

    for kind, c in variants.items():
        joined = " ".join(c.values()).lower()
        assert "days free" not in joined and "7 days" not in joined, \
            f"the {kind} paywall offers free days that no longer exist"
        assert "free plan" in joined or "free" in joined, \
            f"the {kind} paywall does not mention what they still have"

    for kind, c in variants.items():
        assert "from $10/month" in c["subline"]
        # No variant leaves template placeholders behind.
        assert all("{" not in v for v in c.values())
    assert "trial" in variants["trial_ended"]["headline"].lower()
    # The one cohort for whom "your trial ended" is still true — their Stripe
    # trial really did run out — but they land on free, not on nothing.
    assert "free" in variants["trial_ended"]["subline"].lower()
    assert "welcome back" in variants["returning"]["subline"].lower()


def test_grant_trial_sets_status_and_expiry(tmp_path, monkeypatch):
    # Admin-granted timed trial: app-managed 'trialing' + trial_ends_at, no
    # Stripe involvement. The middleware/reaper expire it, so the two fields
    # are the entire contract.
    import time
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    u = users.create("bob", "hunter2hunter2")
    granted = users.grant_trial(u["id"], 30)
    assert granted["subscription_status"] == "trialing"
    assert abs(granted["trial_ends_at"] - (time.time() + 30 * 86400)) < 5
    assert sb.has_access("trialing", False) is True   # gate honours the grant
    # Re-granting extends/replaces the window from now.
    again = users.grant_trial(u["id"], 7)
    assert abs(again["trial_ends_at"] - (time.time() + 7 * 86400)) < 5
    # Unknown user → None, nothing written.
    assert users.grant_trial("missing", 7) is None


def test_has_access_matches_active_statuses():
    assert sb.has_access("active", False) is True
    assert sb.has_access("trialing", False) is True
    assert sb.has_access("expired", False) is False
    assert sb.has_access("inactive", False) is False
    # Admins always have access regardless of subscription state.
    assert sb.has_access("none", True) is True


# ── Customer Portal ("Manage billing") ────────────────────────────────────────
# The portal dies account-wide if no portal configuration was ever saved in the
# Stripe dashboard — a one-time manual step that's easy to miss. These lock the
# self-healing path: build the configuration via API and retry, so "Manage
# billing" never leads nowhere.

def _fresh_portal_state(monkeypatch):
    monkeypatch.setattr(sb, "_portal_config_id", None)


def test_portal_happy_path_needs_no_configuration(monkeypatch):
    _fresh_portal_state(monkeypatch)
    fake = MagicMock()
    fake.billing_portal.sessions.create.return_value = MagicMock(url="https://billing.stripe.com/s/1")
    with patch.object(sb, "_client", return_value=fake):
        url = asyncio.run(sb.create_portal_url("cus_A"))
    assert url == "https://billing.stripe.com/s/1"
    params = fake.billing_portal.sessions.create.call_args.kwargs["params"]
    assert params["customer"] == "cus_A" and "configuration" not in params
    fake.billing_portal.configurations.create.assert_not_called()


def test_portal_self_heals_when_account_has_no_configuration(monkeypatch):
    _fresh_portal_state(monkeypatch)
    monkeypatch.setattr(sb.settings, "stripe_price_id_starter", "price_s")
    monkeypatch.setattr(sb.settings, "stripe_price_id_pro", "price_p")
    fake = MagicMock()
    # First attempt fails the way Stripe actually fails; retry succeeds.
    fake.billing_portal.sessions.create.side_effect = [
        Exception("You can't create a portal session ... save your customer "
                  "portal settings ... default configuration has not been created"),
        MagicMock(url="https://billing.stripe.com/s/2"),
    ]
    fake.billing_portal.configurations.list.return_value = MagicMock(data=[])
    fake.billing_portal.configurations.create.return_value = MagicMock(id="bpc_1")
    fake.prices.retrieve.side_effect = [MagicMock(product="prod_s"), MagicMock(product="prod_p")]
    with patch.object(sb, "_client", return_value=fake):
        url = asyncio.run(sb.create_portal_url("cus_A"))
    assert url == "https://billing.stripe.com/s/2"
    # The created configuration lets users actually manage things: cancel,
    # update card, see invoices, and switch between the two tiers.
    cfg = fake.billing_portal.configurations.create.call_args.kwargs["params"]
    f = cfg["features"]
    assert f["subscription_cancel"] == {"enabled": True, "mode": "at_period_end"}
    assert f["payment_method_update"]["enabled"] and f["invoice_history"]["enabled"]
    assert f["subscription_update"]["products"] == [
        {"product": "prod_s", "prices": ["price_s"]},
        {"product": "prod_p", "prices": ["price_p"]},
    ]
    # Retry pinned the new configuration, and it's cached for next time.
    retry = fake.billing_portal.sessions.create.call_args.kwargs["params"]
    assert retry["configuration"] == "bpc_1"
    assert sb._portal_config_id == "bpc_1"


def test_portal_reuses_existing_configuration_and_unrelated_errors_raise(monkeypatch):
    _fresh_portal_state(monkeypatch)
    fake = MagicMock()
    fake.billing_portal.sessions.create.side_effect = [
        Exception("no configuration saved"),
        MagicMock(url="https://billing.stripe.com/s/3"),
    ]
    fake.billing_portal.configurations.list.return_value = MagicMock(
        data=[MagicMock(id="bpc_existing")])
    with patch.object(sb, "_client", return_value=fake):
        url = asyncio.run(sb.create_portal_url("cus_A"))
    assert url == "https://billing.stripe.com/s/3"
    fake.billing_portal.configurations.create.assert_not_called()   # reuse, don't litter
    # A genuinely unrelated Stripe failure must NOT trigger config creation.
    _fresh_portal_state(monkeypatch)
    fake2 = MagicMock()
    fake2.billing_portal.sessions.create.side_effect = Exception("rate limited")
    import pytest as _pytest
    with patch.object(sb, "_client", return_value=fake2), _pytest.raises(Exception):
        asyncio.run(sb.create_portal_url("cus_A"))
    fake2.billing_portal.configurations.create.assert_not_called()


def test_portal_endpoint_shows_friendly_page_not_500():
    # If Stripe is truly down, a paying user clicking "Manage billing" gets a
    # branded explanation with a way back — never a bare 500 dead end.
    from src.dashboard import api
    assert "support@highlightz.app" in api._PORTAL_ERROR_HTML
    assert 'href="/"' in api._PORTAL_ERROR_HTML
    assert '<meta name="robots" content="noindex">' in api._PORTAL_ERROR_HTML


def test_portal_explains_no_billing_accounts():
    # Admin/trainer/trial accounts have access but no Stripe customer. The old
    # chain (portal → checkout → 'active' guard → dashboard) silently looped,
    # which looked like a broken Manage-billing button. They now get a page
    # that says there's nothing to manage.
    from src.dashboard import api
    assert "no subscription to manage" in api._PORTAL_NO_BILLING_HTML
    assert 'href="/"' in api._PORTAL_NO_BILLING_HTML
    # And the loop's ingredients are locked: admins are minted 'active' with
    # no stripe_customer_id, which is exactly the case the page covers.
    import inspect
    src = inspect.getsource(api.billing_portal)
    assert "_PORTAL_NO_BILLING_HTML" in src and "is_labeler" in src


# ── the post-checkout self-heal ──────────────────────────────────────────────
# Access used to come from the app, so a webhook that never arrived cost us the
# right billing state but not the customer's access. Now access comes from the
# Stripe subscription and nothing else — a webhook endpoint missing
# `customer.subscription.created` means somebody enters a card and lands on a
# locked dashboard. /billing/success asks Stripe directly to close that.

def _success_client(tmp_path, monkeypatch, user_status="none"):
    import base64, json as _j, time as _t
    from itsdangerous import TimestampSigner
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store, trial_ledger

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)

    u = user_store.upsert_twitch_user("tw_sh", "nova", "nova")
    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": u["id"], "username": "nova",
         "is_admin": False, "subscription_status": user_status}).encode())).decode())
    return c, u, user_store, api


def test_billing_success_grants_access_when_the_webhook_never_fires(tmp_path, monkeypatch):
    """The whole point. Without this the customer has paid and has nothing."""
    import time as _t
    c, u, user_store, api = _success_client(tmp_path, monkeypatch)
    ends = int(_t.time()) + 7 * 86400

    async def _lookup(sid):
        assert sid == "cs_test_123"
        return "cus_new", "trialing", ends
    monkeypatch.setattr("src.billing.stripe_billing.subscription_from_checkout_session",
                        _lookup)

    c.get("/billing/success?session_id=cs_test_123", follow_redirects=False)
    after = user_store.get_by_id(u["id"])
    assert after["subscription_status"] == "trialing"
    assert after["stripe_customer_id"] == "cus_new"
    assert after["trial_ends_at"] == ends, \
        "no end date stored — the access gate has nothing to count down to"


def test_the_self_heal_burns_the_free_week_too(tmp_path, monkeypatch):
    """It is granting the trial, so it owes the ledger the same entry the
    webhook would have written — or the week is farmable through this door."""
    import time as _t
    from src.auth import trial_ledger
    c, u, user_store, api = _success_client(tmp_path, monkeypatch)

    async def _lookup(sid):
        return "cus_new", "trialing", int(_t.time()) + 7 * 86400
    monkeypatch.setattr("src.billing.stripe_billing.subscription_from_checkout_session",
                        _lookup)
    c.get("/billing/success?session_id=cs_1", follow_redirects=False)
    assert trial_ledger.has_used_trial("twitch", "tw_sh")


def test_the_self_heal_says_nothing_when_the_webhook_already_won(tmp_path, monkeypatch):
    """The normal case. Firing a "you're all set" toast at somebody whose
    status did not change is noise about an event that did not happen."""
    import time as _t
    c, u, user_store, api = _success_client(tmp_path, monkeypatch, "trialing")
    user_store.update_subscription(u["id"], "cus_new", "trialing",
                                   int(_t.time()) + 7 * 86400)
    said = []

    async def _bcast(msg, user_id=None):
        said.append(msg)
    monkeypatch.setattr(api, "broadcast", _bcast)

    async def _lookup(sid):
        return "cus_new", "trialing", int(_t.time()) + 7 * 86400
    monkeypatch.setattr("src.billing.stripe_billing.subscription_from_checkout_session",
                        _lookup)
    c.get("/billing/success?session_id=cs_1", follow_redirects=False)
    assert said == [], f"toasted about an unchanged status: {said}"


def test_the_self_heal_grants_nothing_on_an_unpaid_session(tmp_path, monkeypatch):
    """Failing open here would hand the product to anyone who can guess a
    session id."""
    c, u, user_store, api = _success_client(tmp_path, monkeypatch)

    async def _lookup(sid):
        return "cus_new", "incomplete", 0
    monkeypatch.setattr("src.billing.stripe_billing.subscription_from_checkout_session",
                        _lookup)
    c.get("/billing/success?session_id=cs_bad", follow_redirects=False)
    assert user_store.get_by_id(u["id"])["subscription_status"] == "none"


def test_the_self_heal_grants_nothing_when_stripe_cannot_be_reached(tmp_path, monkeypatch):
    c, u, user_store, api = _success_client(tmp_path, monkeypatch)

    async def _lookup(sid):
        return None, None, 0
    monkeypatch.setattr("src.billing.stripe_billing.subscription_from_checkout_session",
                        _lookup)
    r = c.get("/billing/success?session_id=cs_x", follow_redirects=False)
    assert r.status_code in (302, 307)          # never a 500 in front of a payer
    assert user_store.get_by_id(u["id"])["subscription_status"] == "none"


def test_a_paid_subscription_with_no_trial_clears_the_trial_date(tmp_path, monkeypatch):
    import time as _t
    c, u, user_store, api = _success_client(tmp_path, monkeypatch)

    async def _lookup(sid):
        return "cus_new", "active", 0
    monkeypatch.setattr("src.billing.stripe_billing.subscription_from_checkout_session",
                        _lookup)
    c.get("/billing/success?session_id=cs_1", follow_redirects=False)
    after = user_store.get_by_id(u["id"])
    assert after["subscription_status"] == "active"
    assert after["trial_ends_at"] == 0


# ── the webhook, end to end ──────────────────────────────────────────────────
# Mutation testing found these missing. sync_subscription_event and
# extract_trial_end were each covered in isolation, and /billing/success was
# covered, but nothing drove _process_stripe_event itself — which is the
# PRIMARY path that grants access. Five separate breakages walked straight
# through: the trial end never stored, an unrelated event blanking it, the free
# week never burned, and the two status paths.

import time as _time

import pytest


@pytest.fixture
def hook(tmp_path, monkeypatch):
    """A user with a Stripe customer, ready to receive webhook events."""
    from src.dashboard import api
    from src.auth import users as user_store, trial_ledger

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(trial_ledger, "_LEDGER_FILE", tmp_path / "trials.json")
    monkeypatch.setattr(api, "_stripe_processed", {})

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)
    monkeypatch.setattr(api, "_enforce_stream_limit", _noop, raising=False)
    # The outside world. None of it is what these tests are about.
    monkeypatch.setattr(sb, "customer_email", _noop)
    monkeypatch.setattr(sb, "cancel_duplicate_subscriptions", _noop)

    async def _others(*a, **k):
        return False
    monkeypatch.setattr(sb, "has_other_live_subscription", _others)
    monkeypatch.setattr(sb, "plan_for_price", lambda p: None)
    monkeypatch.setattr(sb, "extract_promo_id", lambda e: None)

    u = user_store.upsert_twitch_user("tw_hook", "nova", "nova")
    user_store.update_subscription(u["id"], "cus_hook", "none")
    return api, user_store, trial_ledger, u


def _fire(api, event):
    import asyncio
    return asyncio.run(api._process_stripe_event(event, _time.time(), event["id"]))


def _ev(etype, status, user, *, trial_end=None, cust="cus_hook", eid="evt_x"):
    obj = {"id": "sub_1", "customer": cust, "status": status,
           "metadata": {"user_id": user["id"]}}
    if trial_end is not None:
        obj["trial_end"] = trial_end
    return {"id": eid, "type": etype, "data": {"object": obj}}


def test_the_webhook_grants_a_stripe_trial_and_stores_its_end_date(hook):
    """The countdown the dashboard shows is this number. Without it the user
    sees "0 days left" on day one of a trial they just paid for."""
    api, store, ledger, u = hook
    ends = int(_time.time()) + 7 * 86400
    _fire(api, _ev("customer.subscription.created", "trialing", u, trial_end=ends))
    after = store.get_by_id(u["id"])
    assert after["subscription_status"] == "trialing"
    assert after["trial_ends_at"] == ends, "the trial end date was not stored"


def test_the_webhook_burns_the_free_week_when_stripe_starts_one(hook):
    """Not at signup — a look-around must not cost the week — and not at
    checkout creation, because an abandoned session costs nothing. Here."""
    api, store, ledger, u = hook
    _fire(api, _ev("customer.subscription.created", "trialing", u,
                   trial_end=int(_time.time()) + 7 * 86400))
    assert ledger.has_used_trial("twitch", "tw_hook"), \
        "the free week was never recorded — it is farmable"


def test_the_trial_converting_clears_the_end_date(hook):
    """Day 7: the card is charged. A leftover date has the dashboard counting
    down to a day that means nothing."""
    api, store, ledger, u = hook
    ends = int(_time.time()) + 7 * 86400
    _fire(api, _ev("customer.subscription.created", "trialing", u, trial_end=ends))
    _fire(api, _ev("customer.subscription.updated", "active", u, eid="evt_y"))
    after = store.get_by_id(u["id"])
    assert after["subscription_status"] == "active"
    assert after["trial_ends_at"] == 0


def test_an_unrelated_event_does_not_blank_a_live_trials_end_date(hook):
    """The reason the trial end is None rather than 0 on other statuses.
    Blanking it would drop the user's countdown to zero — and, before the
    access gate learned to require `> 0`, would have expired them outright."""
    api, store, ledger, u = hook
    ends = int(_time.time()) + 7 * 86400
    _fire(api, _ev("customer.subscription.created", "trialing", u, trial_end=ends))
    _fire(api, _ev("customer.subscription.updated", "past_due", u, eid="evt_z"))
    assert store.get_by_id(u["id"])["trial_ends_at"] == ends, \
        "an unrelated event wiped the trial end date"


def test_a_trial_with_no_end_date_from_stripe_still_grants_access(hook):
    """Belt and braces on the same guard, from the webhook side."""
    api, store, ledger, u = hook
    _fire(api, _ev("customer.subscription.created", "trialing", u))
    after = store.get_by_id(u["id"])
    assert after["subscription_status"] == "trialing"
    from src.billing.plans import get_plan
    assert get_plan(after) == "pro"


def test_me_tells_a_card_up_front_trial_from_an_admin_comp(hook, monkeypatch):
    """They need opposite advice: one converts by itself and wants a cancel
    link, the other really does stop and wants a Subscribe button. Getting it
    backwards sends a paying customer into a second checkout."""
    import base64, json as _j
    from itsdangerous import TimestampSigner
    from fastapi.testclient import TestClient
    api, store, ledger, u = hook

    def _me_for(user_id):
        c = TestClient(api.app)
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": user_id, "username": "nova",
             "is_admin": False, "subscription_status": "trialing"}).encode())).decode())
        return c.get("/me").json()

    # Card up front: the webhook has linked a Stripe customer.
    _fire(api, _ev("customer.subscription.created", "trialing", u,
                   trial_end=int(_time.time()) + 7 * 86400))
    assert _me_for(u["id"])["trial_converts"] is True

    # Admin comp: app-managed, no Stripe customer anywhere.
    comp = store.upsert_twitch_user("tw_comp2", "comped", "comped")
    store.grant_trial(comp["id"], days=14)
    assert _me_for(comp["id"])["trial_converts"] is False


def test_the_checkout_guard_records_a_live_trial_as_trialing(hook, monkeypatch):
    """The webhook-latency self-heal inside /billing/checkout. It used to write
    "active" for both live states, which tells somebody mid-trial that they are
    being billed — and this path exists precisely for the seconds right after a
    card-up-front trial starts, which is now the likeliest time to hit it."""
    import base64, json as _j
    from itsdangerous import TimestampSigner
    from fastapi.testclient import TestClient
    api, store, ledger, u = hook

    async def _live(cust):
        return "trialing"
    monkeypatch.setattr(sb, "live_subscription_status", _live)
    # Without these the endpoint 503s ("Stripe not configured") before it ever
    # reaches the self-heal, and the test would pass for the wrong reason.
    monkeypatch.setattr(api.settings, "stripe_secret_key", "sk_test")
    monkeypatch.setattr(api.settings, "stripe_price_id_pro", "price_pro")

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": u["id"], "username": "nova",
         "is_admin": False, "subscription_status": "none"}).encode())).decode())
    c.get("/billing/checkout?plan=pro", follow_redirects=False)
    assert store.get_by_id(u["id"])["subscription_status"] == "trialing", \
        "a live trial was recorded as an active paid subscription"
