"""Where each account stopped on the way to paying.

ASKED FOR AFTER A REAL INCIDENT: somebody subscribed and had no access, and
there was no way to see that from the admin panel — a locked account that never
opened checkout and one that paid and was never linked resolve to the SAME
plan, so the table showed them identically. One of those is a person owed a
refund.

The stage answers "how far did they get", which get_plan does not and should
not. It reads local state only, so it reports what the APP believes — which is
what the customer is experiencing. scripts/why_no_access.py is the tool that
compares that against Stripe.
"""

import time

import pytest

from src.billing.plans import FUNNEL_LABELS, FUNNEL_STAGES, funnel_stage


def u(**kw):
    return {"id": "u1", "username": "nova", **kw}


# ── the stages that matter for the incident ──────────────────────────────────

def test_signed_up_and_never_opened_checkout():
    """Nothing is broken for this person. They just have not paid."""
    assert funnel_stage(u(subscription_status="none")) == "signed_up"


def test_reached_the_card_form_and_left():
    """The stall that was previously invisible: no local state changed when
    somebody clicked through to Stripe, so this and the row above were the same
    row in the database."""
    assert funnel_stage(u(subscription_status="none",
                          checkout_started_at=time.time())) == "checkout_started"


def test_a_stripe_customer_with_no_subscription_is_a_dropped_checkout():
    """They got as far as Stripe creating a customer. Card declined, or they
    closed the tab on the payment step."""
    assert funnel_stage(u(subscription_status="none",
                          stripe_customer_id="cus_1")) == "checkout_dropped"


def test_an_unconfirmed_first_payment_is_a_dropped_checkout_not_a_dunning_card():
    """`incomplete` means the FIRST payment never confirmed — they are still
    inside checkout. Calling it "card failing" would send you chasing a
    customer who never became one."""
    assert funnel_stage(u(subscription_status="incomplete",
                          stripe_customer_id="cus_1")) == "checkout_dropped"


def test_past_due_is_a_real_customer_whose_card_is_failing():
    assert funnel_stage(u(subscription_status="past_due",
                          stripe_customer_id="cus_1")) == "past_due"


def test_trialing_and_paying_are_distinct():
    assert funnel_stage(u(subscription_status="trialing")) == "trialing"
    assert funnel_stage(u(subscription_status="active")) == "paying"


def test_a_cancelled_customer_is_lapsed_not_a_dropped_checkout():
    assert funnel_stage(u(subscription_status="canceled",
                          stripe_customer_id="cus_1")) == "lapsed"


def test_staff_are_not_in_the_funnel():
    assert funnel_stage(u(is_admin=True)) == "staff"
    assert funnel_stage(u(is_labeler=True)) == "staff"


def test_frozen_accounts_are_not_reported_as_a_stall():
    """A pre-cutover or grandfathered account sitting on free is where it means
    to be. Filing it as "lapsed" or "signed up, never paid" puts it on a list of
    people to chase, and their terms are deliberately frozen."""
    assert funnel_stage(u(subscription_status="none",
                          grandfathered=True)) == "legacy"
    assert funnel_stage(u(subscription_status="none",
                          pre_card_cutover=True)) == "legacy"


def test_every_stage_has_a_label_and_every_label_a_stage():
    assert set(FUNNEL_LABELS) == set(FUNNEL_STAGES)


def test_no_user_is_not_a_crash():
    assert funnel_stage(None) in FUNNEL_STAGES


# ── the signal that makes checkout_started possible ──────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    return users


def test_the_checkout_stamp_is_first_touch_not_last(store):
    """It answers "did they ever reach the card form", not "when did they last
    open it". Overwriting would make it a last-seen field that answers neither
    — and the first attempt is the one that dates the stall."""
    user = store.upsert_twitch_user("tw1", "nova", "nova")
    store.mark_checkout_started(user["id"])
    first = store.get_by_id(user["id"])["checkout_started_at"]
    assert first > 0
    time.sleep(0.01)
    store.mark_checkout_started(user["id"])
    assert store.get_by_id(user["id"])["checkout_started_at"] == first


def test_stamping_a_missing_user_is_not_an_error(store):
    store.mark_checkout_started("ghost")          # must not raise


def test_the_stamp_happens_only_after_the_session_really_exists():
    """Stamping before create_checkout_url would count people whose checkout we
    FAILED to create as people who reached the card form and walked away — the
    opposite conclusion, drawn from our own outage."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.billing_checkout)
    assert src.index("create_checkout_url(") < src.index("mark_checkout_started("), \
        "the checkout stamp is written before the session is created"


# ── through the admin endpoint ───────────────────────────────────────────────

def test_the_admin_table_carries_the_stage(monkeypatch, tmp_path):
    import base64, json as _j
    from itsdangerous import TimestampSigner
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_clips", {})

    admin = user_store.upsert_twitch_user("tw_a", "boss", "boss", is_admin=True)
    stalled = user_store.upsert_twitch_user("tw_b", "nova", "nova")
    user_store.mark_checkout_started(stalled["id"])

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": admin["id"], "username": "boss",
         "is_admin": True}).encode())).decode())

    rows = {r["username"]: r for r in c.get("/admin/users").json()}
    assert rows["nova"]["funnel_stage"] == "checkout_started"
    assert rows["nova"]["funnel_label"] == FUNNEL_LABELS["checkout_started"]
    assert rows["boss"]["funnel_stage"] == "staff"


def test_the_panel_renders_the_stage_and_can_filter_by_it():
    from src.dashboard.api import ADMIN_HTML
    assert "stageNote(u)" in ADMIN_HTML, "the stage is never drawn"
    assert "data-f=\"stalled\"" in ADMIN_HTML, "there is no way to list the stalls"
    assert "STALLED.includes(u.funnel_stage" in ADMIN_HTML


def test_the_stage_line_is_hidden_for_people_it_would_only_repeat():
    """A paying customer's stage is "paying" and the plan pill already says so.
    Printing it again on every row is noise that buries the rows that matter."""
    from src.dashboard.api import ADMIN_HTML
    i = ADMIN_HTML.index("function stageNote(")
    body = ADMIN_HTML[i:ADMIN_HTML.index("function userState(", i)]
    assert "STALLED.includes(st)" in body and "return ''" in body


# ── the diagnostic script ────────────────────────────────────────────────────
# It is the thing you reach for at the worst moment, so it must not be the
# thing that breaks. These exercise every branch of its verdict logic without
# touching Stripe.

def _diag(user, subs=()):
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "why_no_access", pathlib.Path(__file__).parent.parent / "scripts" / "why_no_access.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sv = {"by_customer": list(subs), "by_metadata": [], "by_email": [], "error": None}
    return mod.diagnose(user, sv)


def _sub(status="active", sid="sub_1", cust="cus_1"):
    return {"id": sid, "status": status, "customer": cust, "items": {"data": []}}


def test_the_script_names_the_paid_but_unlinked_case():
    """The incident. Stripe has a live subscription, the account has no
    customer id, and the person is locked out with money taken."""
    stage, advice = _diag(u(subscription_status="none"), [_sub("active")])
    assert stage == "PAID BUT NOT LINKED"
    assert "webhook never landed" in advice


def test_the_script_names_the_paid_but_locked_out_case():
    stage, _ = _diag(u(subscription_status="canceled", stripe_customer_id="cus_1"),
                     [_sub("active")])
    assert stage == "PAID BUT LOCKED OUT"


def test_the_script_names_a_trial_stripe_started_that_we_did_not_record():
    stage, _ = _diag(u(subscription_status="none", stripe_customer_id="cus_1"),
                     [_sub("trialing")])
    assert stage == "TRIAL NOT RECORDED"


def test_the_script_does_not_cry_wolf_when_everything_agrees():
    stage, _ = _diag(u(subscription_status="active", stripe_customer_id="cus_1"),
                     [_sub("active")])
    assert stage == "OK"
    stage, _ = _diag(u(subscription_status="trialing", stripe_customer_id="cus_1"),
                     [_sub("trialing")])
    assert stage == "OK"


def test_the_script_separates_the_two_kinds_of_not_paying():
    assert _diag(u(subscription_status="none"))[0] == "NEVER STARTED CHECKOUT"
    assert _diag(u(subscription_status="none",
                   checkout_started_at=time.time()))[0] == "LEFT AT THE CARD FORM"
    assert _diag(u(subscription_status="none",
                   stripe_customer_id="cus_1"))[0] == "CHECKOUT ABANDONED"


def test_the_script_flags_access_stripe_cannot_account_for():
    stage, _ = _diag(u(subscription_status="active", stripe_customer_id="cus_1"))
    assert stage == "GHOST ACCESS"


def test_the_script_reports_rather_than_guesses_when_stripe_is_unreachable():
    """Silence from Stripe is not evidence of anything. Reporting "never paid"
    during an outage is how you refund somebody who is paying."""
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location(
        "why_no_access", pathlib.Path(__file__).parent.parent / "scripts" / "why_no_access.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sv = {"by_customer": [], "by_metadata": [], "by_email": [], "error": "boom"}
    stage, advice = mod.diagnose(u(subscription_status="none"), sv)
    assert stage == "UNKNOWN" and "boom" in advice


def test_the_script_is_read_only_without_fix():
    """It is run in a panic. It must not change anything until asked."""
    import inspect, pathlib
    src = (pathlib.Path(__file__).parent.parent / "scripts" / "why_no_access.py").read_text()
    body = src[src.index("def main("):]
    assert "if args.fix:" in body
    # The only writes in the file live in apply_fix, which main calls only
    # under --fix.
    for writer in ("update_subscription", "set_plan"):
        assert src.count(writer) == 1, \
            f"{writer} is called outside apply_fix — the script is not read-only"
