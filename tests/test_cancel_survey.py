"""The cancellation survey (owner, 2026-11-18: "When users end their
subscription make them take a survey").

Nobody cancels on this site: "Manage billing" opens Stripe's page and the
cancel button is there. So the site hears of it from Stripe's webhook, marks
the account due, and the dashboard asks before anything else when Stripe
sends them back. What these hold:

  1. what a subscription event means: cancel pressed, cancel undone, ended
     because they asked, ended because the card failed (never asked);
  2. one question per cancellation, however many events Stripe sends;
  3. an answer given on Stripe's own cancel page counts and is not asked twice;
  4. the whole way through the real webhook, and that a broken survey can
     never break billing;
  5. the answer endpoint, /me and the live events (no refresh);
  6. the modal cannot be skipped and its answers are the server's;
  7. the admin read-out.
"""

import asyncio
import base64
import inspect
import json
import re
import time

import pytest

from src.auth import users as user_store
from src.billing import cancel_survey as cs
from src.dashboard import api
from src.dashboard.aurora_html import DASHBOARD_HTML as PAGE


def _sub(status="active", **kw):
    return {"id": "sub_A", "customer": "cus_1", "status": status,
            "metadata": {"user_id": "u1"},
            "items": {"data": [{"price": {"id": "price_pro"}, "current_period_end": 2_000_000_000}]},
            **kw}


CANCELLED = dict(cancel_at_period_end=True, canceled_at=1_700_000_000,
                 cancellation_details={"reason": "cancellation_requested",
                                       "feedback": None, "comment": None})


# ── 1. what an event means ───────────────────────────────────────────────────

def test_pressing_cancel_is_the_moment_to_ask():
    info = cs.read(_sub(**CANCELLED))
    assert info["scheduled"] and info["token"] == "sub_A:1700000000"
    assert info["ends_at"] == 2_000_000_000          # read off the item on newer API versions
    assert cs.decide(info, "active", "active") == "ask"
    assert cs.decide(cs.read(_sub("trialing", **CANCELLED)), "trialing", "trialing") == "ask"
    # A cancel date instead of the flag is the same thing.
    assert cs.decide(cs.read(_sub(cancel_at=1_800_000_000)), "active", "active") == "ask"
    assert cs.read(_sub(cancel_at=1_800_000_000))["ends_at"] == 1_800_000_000


def test_undoing_the_cancel_takes_the_question_back():
    assert cs.decide(cs.read(_sub()), "active", "active") == "unask"


def test_an_ending_they_asked_for_is_asked_about_and_a_failed_card_never_is():
    asked = _sub("canceled", canceled_at=5, cancellation_details={"reason": "cancellation_requested"})
    assert cs.decide(cs.read(asked), "inactive", "active") == "ask"
    # Stripe gave up on the card: that is not somebody deciding to leave.
    failed = _sub("canceled", canceled_at=5, cancellation_details={"reason": "payment_failed"})
    assert cs.decide(cs.read(failed), "inactive", "active") == ""
    assert cs.decide(cs.read(failed), "inactive", "past_due") == ""
    assert cs.decide(cs.read(asked), "inactive", "past_due") == ""
    # Our own clean-up of a signup that was refunded: already inactive.
    assert cs.decide(cs.read(asked), "inactive", "inactive") == ""
    assert cs.decide(cs.read(_sub("past_due")), "past_due", "active") == ""


def test_an_answer_given_on_stripes_page_is_read_in_our_words():
    for theirs, ours in (("too_expensive", "too_expensive"), ("unused", "not_using"),
                         ("low_quality", "clips_quality"), ("missing_features", "missing_feature"),
                         ("too_complex", "hard_to_use"), ("switched_service", "switched"),
                         ("other", "other"), ("something_new_from_stripe", "other")):
        d = {**CANCELLED, "cancellation_details": {"reason": "cancellation_requested",
                                                   "feedback": theirs, "comment": "too pricey for me"}}
        info = cs.read(_sub(**d))
        assert info["reason"] == ours and info["detail"] == "too pricey for me"
        assert cs.decide(info, "active", "active") == "record"
        assert ours in user_store.CANCEL_REASONS
    only_comment = cs.read(_sub(**{**CANCELLED, "cancellation_details": {"comment": "moving house"}}))
    assert only_comment["reason"] == "other" and cs.decide(only_comment, "active", "active") == "record"


# ── 2-3. the store ───────────────────────────────────────────────────────────

@pytest.fixture
def store(monkeypatch, tmp_path):
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    (tmp_path / "users.json").write_text(json.dumps([
        {"id": "u1", "username": "nova", "stripe_customer_id": "cus_1",
         "subscription_status": "active", "plan": "pro", "created_at": time.time()},
        {"id": "u2", "username": "kestrel", "subscription_status": "none", "created_at": time.time()}]))
    return lambda uid="u1": user_store.get_by_id(uid)


def test_one_question_per_cancellation(store):
    assert user_store.ask_cancel_survey("u1", "sub_A:1", "Pro", 99) is True
    assert user_store.cancel_survey_due(store()) == {"plan": "Pro", "ends_at": 99.0}
    assert user_store.ask_cancel_survey("u1", "sub_A:1", "Pro", 99) is False       # Stripe repeats itself
    a = user_store.set_cancel_survey("u1", "too_expensive", "  $10 would   work  ")
    assert a["reason"] == "too_expensive" and a["detail"] == "$10 would work" and a["plan"] == "Pro"
    assert user_store.cancel_survey_due(store()) is None
    assert user_store.ask_cancel_survey("u1", "sub_A:1") is False                   # answered: never again
    # They came back, subscribed again and cancelled again: a new question.
    assert user_store.ask_cancel_survey("u1", "sub_B:7", "Studio") is True
    user_store.set_cancel_survey("u1", "temporary")
    assert [x["reason"] for x in user_store.cancel_surveys_of(store())] == ["too_expensive", "temporary"]
    assert user_store.cancel_survey_due(store("u2")) is None


def test_an_answer_needs_a_question_and_a_real_reason(store):
    with pytest.raises(ValueError, match="nothing to answer"):
        user_store.set_cancel_survey("u1", "too_expensive")
    user_store.ask_cancel_survey("u1", "t")
    with pytest.raises(ValueError, match="Pick one"):
        user_store.set_cancel_survey("u1", "because")
    with pytest.raises(ValueError, match="Tell us"):
        user_store.set_cancel_survey("u1", "other", " ")
    assert user_store.cancel_survey_due(store()) is not None      # still due after a bad answer
    assert user_store.set_cancel_survey("u1", "other", "x" * 900)["detail"] == "x" * 500
    assert user_store.set_cancel_survey("ghost", "other", "hello") is None


def test_changing_their_mind_withdraws_only_an_unanswered_question(store):
    user_store.ask_cancel_survey("u1", "t")
    assert user_store.unask_cancel_survey("u1") is True and user_store.cancel_survey_due(store()) is None
    assert user_store.unask_cancel_survey("u1") is False
    # Withdrawn, so pressing cancel again on the SAME subscription asks again
    # (Stripe stamps a new time, hence a new token).
    assert user_store.ask_cancel_survey("u1", "t2") is True


def test_stripes_answer_counts_once_and_closes_the_question(store):
    user_store.ask_cancel_survey("u1", "sub_A:1", "Pro")
    assert user_store.record_cancel_feedback("u1", "sub_A:1", "switched", "went to X tool") is True
    assert user_store.cancel_survey_due(store()) is None
    got = user_store.cancel_surveys_of(store())
    assert got[0]["source"] == "stripe" and got[0]["plan"] == "Pro" and got[0]["reason"] == "switched"
    assert user_store.record_cancel_feedback("u1", "sub_A:1", "switched") is False
    assert user_store.ask_cancel_survey("u1", "sub_A:1") is False


# ── 4. through the real webhook ──────────────────────────────────────────────

@pytest.fixture
def wired(store, monkeypatch):
    from src.billing import stripe_billing
    from config.settings import settings
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_stub")
    monkeypatch.setattr(settings, "stripe_price_id_pro", "price_pro")
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_publish_remove_stream", _noop)
    monkeypatch.setattr(stripe_billing, "customer_email", _noop)
    state = {"subs": []}

    class _R:
        def __init__(self, d): self.data = d

    class _C:
        def __init__(self): self.subscriptions = self
        def list(self, params=None): return _R(state["subs"])
    monkeypatch.setattr(stripe_billing, "_client", lambda: _C())

    def fire(sub, kind="customer.subscription.updated"):
        state["subs"] = [] if sub["status"] == "canceled" else [{"id": sub["id"], "status": sub["status"]}]
        api._stripe_processed.clear()
        ev = {"id": f"evt_{len(sent)}_{time.time()}", "type": kind, "data": {"object": sub}}
        return asyncio.run(api._process_stripe_event(ev, time.time(), ev["id"]))
    return type("W", (), {"fire": staticmethod(fire), "sent": sent, "user": staticmethod(store)})


def test_cancelling_on_stripe_puts_the_question_on_the_dashboard(wired):
    wired.fire(_sub(**CANCELLED))
    u = wired.user()
    assert u["subscription_status"] == "active"                   # they keep what they paid for
    assert user_store.cancel_survey_due(u) == {"plan": "Pro", "ends_at": 2_000_000_000.0}
    assert ("billing_changed", "u1") in wired.sent                # an open tab re-reads /me
    wired.sent.clear()
    wired.fire(_sub(**CANCELLED))                                 # Stripe sends it again
    assert ("billing_changed", "u1") not in wired.sent
    # The period runs out: the subscription ends. Same cancellation, no second question.
    user_store.set_cancel_survey("u1", "not_using")
    wired.fire(_sub("canceled", canceled_at=1_700_000_000,
                    cancellation_details={"reason": "cancellation_requested"}),
               "customer.subscription.deleted")
    assert wired.user()["subscription_status"] == "inactive"
    assert user_store.cancel_survey_due(wired.user()) is None
    assert len(user_store.cancel_surveys_of(wired.user())) == 1


def test_keeping_the_plan_after_all_makes_the_question_go(wired):
    wired.fire(_sub(**CANCELLED))
    wired.sent.clear()
    wired.fire(_sub())                                            # "Don't cancel" on Stripe's page
    assert user_store.cancel_survey_due(wired.user()) is None
    assert ("billing_changed", "u1") in wired.sent


def test_an_immediate_cancel_is_asked_about_too(wired):
    wired.fire(_sub("canceled", canceled_at=42,
                    cancellation_details={"reason": "cancellation_requested"}),
               "customer.subscription.deleted")
    assert wired.user()["subscription_status"] == "inactive"
    assert user_store.cancel_survey_due(wired.user())["plan"] == "Pro"


def test_a_card_stripe_gave_up_on_is_not_asked_why_it_cancelled(wired):
    wired.fire(_sub("past_due"))
    wired.fire(_sub("canceled", canceled_at=42, cancellation_details={"reason": "payment_failed"}),
               "customer.subscription.deleted")
    assert wired.user()["subscription_status"] == "inactive"
    assert user_store.cancel_survey_due(wired.user()) is None


def test_stripes_own_reason_step_is_the_survey(wired):
    wired.fire(_sub(**CANCELLED))                                 # cancel confirmed...
    wired.fire(_sub(**{**CANCELLED, "cancellation_details": {     # ...then its reason page answered
        "reason": "cancellation_requested", "feedback": "too_expensive", "comment": "student budget"}}))
    u = wired.user()
    assert user_store.cancel_survey_due(u) is None
    a = user_store.cancel_surveys_of(u)[0]
    assert (a["reason"], a["detail"], a["source"], a["plan"]) == ("too_expensive", "student budget", "stripe", "Pro")


def test_a_broken_survey_can_never_break_billing(wired, monkeypatch):
    """The hook sits in the webhook that hands out and takes away access. If
    it throws, the subscription change must still be applied."""
    def boom(*a, **k):
        raise RuntimeError("survey store is on fire")
    monkeypatch.setattr(cs, "read", boom)
    assert wired.fire(_sub("canceled", canceled_at=1), "customer.subscription.deleted") == {"received": True}
    assert wired.user()["subscription_status"] == "inactive"
    assert ("subscription_expired", "u1") in wired.sent
    src = inspect.getsource(api._process_stripe_event)
    assert src.index("await _cancel_survey_from_event(") > src.index("user_id = apply_subscription_event(")
    assert 'log.exception("cancel_survey_hook_failed"' in src


# ── 5. the endpoint, /me and the events ──────────────────────────────────────

@pytest.fixture
def client(store, monkeypatch):
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
        {"auth": True, "user_id": "u1", "username": "nova", "is_admin": False,
         "subscription_status": "active"}).encode())).decode())
    c.sent = sent
    return c


def test_the_answer_is_saved_and_every_tab_is_told(client):
    assert client.get("/me").json()["cancel_survey_due"] is None
    assert client.post("/billing/cancel-survey", json={"reason": "too_expensive"}).status_code == 400
    user_store.ask_cancel_survey("u1", "sub_A:1", "Pro", 2_000_000_000)
    assert client.get("/me").json()["cancel_survey_due"] == {"plan": "Pro", "ends_at": 2_000_000_000.0}
    assert client.post("/billing/cancel-survey", json={"reason": "nope"}).status_code == 400
    assert client.post("/billing/cancel-survey", json={"reason": "other"}).status_code == 400
    assert client.post("/billing/cancel-survey", json=["x"]).status_code == 400
    r = client.post("/billing/cancel-survey", json={"reason": "clips_quality", "detail": "missed the big plays"})
    assert r.status_code == 200 and r.json() == {"cancel_survey_due": None}
    assert client.sent == [("cancel_survey_saved", "u1")]         # this account only
    assert client.get("/me").json()["cancel_survey_due"] is None
    assert client.post("/billing/cancel-survey", json={"reason": "clips_quality"}).status_code == 400


def test_signed_out_nobody_can_answer(store):
    from fastapi.testclient import TestClient
    c = TestClient(api.app, base_url="https://testserver")
    r = c.post("/billing/cancel-survey", json={"reason": "other", "detail": "hi"}, follow_redirects=False)
    assert r.status_code in (302, 303, 307, 401, 403)


# ── 6. the modal ─────────────────────────────────────────────────────────────

def _fn(name):
    at = PAGE.index("function " + name + "(")
    return PAGE[at:PAGE.index("\nfunction ", at + 10)]


def test_the_modal_offers_exactly_the_servers_answers():
    block = PAGE[PAGE.index("const CANCEL_REASONS = ["):]
    block = block[:block.index("];")]
    keys = re.findall(r"\['([a-z_]+)',", block)
    assert tuple(keys) == user_store.CANCEL_REASONS
    assert user_store.CANCEL_NEEDS_DETAIL == ("other",)


def test_the_modal_cannot_be_skipped():
    m = _fn("CancelSurveyModal")
    assert "fetch('/billing/cancel-survey'" in m
    assert "disabled={busy || !ready}" in m
    assert "const ready = !!reason && (!needsLine || detail.trim().length >= 2);" in m
    # No way out but an answer: no close control, no Escape, no backdrop click.
    for banned in ("Escape", "onClose", "Skip", "Not now", "&times;", "onDismiss"):
        assert banned not in m, banned
    assert 'className="rd-ann-bg" role="dialog" aria-modal="true"' in m
    assert "onClick" not in m[m.index('className="rd-ann-bg"'):m.index('className="rd-onb glass"')]
    # A failed save keeps it open with the reason, and nothing says refresh.
    assert "setErr(String(e.message || e));" in m and "refresh" not in m.lower()
    # The one other door: keeping the plan, which un-asks it from the server.
    assert 'href="/billing/portal"' in m and "Changed your mind? Keep your plan" in m


def test_it_is_derived_from_me_and_follows_the_socket():
    assert "const needsCancelSurvey = !onbPreview && !!(me && me.user_id && me.cancel_survey_due) && !needsFoundUs;" in PAGE
    assert PAGE.count("{needsCancelSurvey && <CancelSurveyModal due={me.cancel_survey_due} onDone={cancelSurveyDone}/>}") == 2
    assert "else if(msg.event==='cancel_survey_saved'){" in PAGE
    # billing_changed (what the webhook sends) already re-reads /me.
    at = PAGE.index("msg.event==='billing_changed'")
    assert "fetch('/me')" in PAGE[at:at + 500]
    # One modal at a time: it goes in front of announcements, offers, onboarding.
    assert "announcements.length > 0 && !needsFoundUs && !needsCancelSurvey &&" in PAGE
    assert "!needsOnboarding && !needsFoundUs && !needsCancelSurvey && me && me.offer &&" in PAGE
    assert "needsOnboarding && !needsFoundUs && !needsCancelSurvey && !wake &&" in PAGE


def test_every_event_the_survey_sends_has_a_handler():
    src = inspect.getsource(api._cancel_survey_from_event) + inspect.getsource(api.post_cancel_survey)
    for ev in set(re.findall(r'"event": "([a-z_]+)"', src)):
        assert f"msg.event==='{ev}'" in PAGE, ev


# ── 7. the admin read-out ────────────────────────────────────────────────────

def test_the_admin_sees_reasons_words_and_who_is_still_to_answer(store, monkeypatch):
    monkeypatch.setattr(api, "_require_admin", lambda request: None)
    user_store.ask_cancel_survey("u1", "a", "Pro")
    user_store.set_cancel_survey("u1", "too_expensive", "ten dollars")
    user_store.ask_cancel_survey("u1", "b", "Pro")
    user_store.record_cancel_feedback("u1", "b", "switched", "")
    user_store.ask_cancel_survey("u2", "c", "Studio")
    d = asyncio.run(api.admin_cancellations(None))
    assert d["answered"] == 2 and d["reasons"]["too_expensive"] == 1 and d["reasons"]["switched"] == 1
    assert set(d["reasons"]) == set(user_store.CANCEL_REASONS)
    assert {(r["user"], r["reason"], r["source"]) for r in d["rows"]} == {
        ("nova", "too_expensive", "user"), ("nova", "switched", "stripe")}
    assert [(w["user"], w["plan"]) for w in d["waiting"]] == [("kestrel", "Studio")]
    assert "email" not in json.dumps(d) and "cus_" not in json.dumps(d)


def test_the_admin_page_has_the_tab_and_labels_for_every_reason():
    h = api.ADMIN_HTML
    assert 'data-tab="cancellations"' in h and 'id="panel-cancellations"' in h
    assert "if(b.dataset.tab === 'cancellations') loadCancellations();" in h
    block = h[h.index("const CX_LABELS = {"):]
    block = block[:block.index("};")]
    assert tuple(re.findall(r"^\s*([a-z_]+):", block, re.M)) == user_store.CANCEL_REASONS
    # Everything a person typed goes through esc().
    fn = h[h.index("async function loadCancellations(){"):h.index("function cancellationsOpen(){")]
    assert "esc(r.detail)" in fn and "esc(r.user)" in fn and "esc(r.plan)" in fn
