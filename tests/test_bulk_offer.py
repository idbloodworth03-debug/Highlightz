"""One discount offer to everyone not paying (owner, 2026-10-05).

"notify all users that are not on a paid plan and offer them a 50% off first
month like I did with the one user … can you mass announce it for every user
that is not paying … code CLIPPER"

What these defend:
  1. the audience: free, lapsed and left-at-checkout accounts get it; staff,
     paying/comped (active) and anyone with a live Stripe subscription
     (past_due, incomplete) do not — a second Checkout would double-bill;
  2. a dry run only counts; the real send stores the offer and each recipient's
     tab hears `offer_changed` (scoped to them, already handled by the dashboard);
  3. /me then carries the offer, so the existing claim popup shows it;
  4. it can be taken back by code; only an admin can do either;
  5. the admin controls are wired.
"""

import base64
import json

import pytest
from itsdangerous import TimestampSigner

from src.auth import users as user_store

PEOPLE = {}


def _fresh():
    PEOPLE.clear()
    for uid, extra in {
        "boss":    {"is_admin": True, "subscription_status": "active"},
        "trainer": {"is_labeler": True, "subscription_status": "none"},
        "free":    {"subscription_status": "none"},
        "lapsed":  {"subscription_status": "canceled", "stripe_customer_id": "cus_1"},
        "expired": {"subscription_status": "expired"},
        "trial":   {"subscription_status": "trialing", "trial_ends_at": 9e12},
        "paying":  {"subscription_status": "active", "stripe_customer_id": "cus_2"},
        "card":    {"subscription_status": "past_due", "stripe_customer_id": "cus_3"},
        "half":    {"subscription_status": "incomplete", "stripe_customer_id": "cus_4"},
    }.items():
        PEOPLE[uid] = {"id": uid, "username": uid, **extra}


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    _fresh()
    monkeypatch.setattr(user_store, "_load", lambda: list(PEOPLE.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        p = PEOPLE[uid]
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": p.get("is_admin", False),
             "subscription_status": p.get("subscription_status", "none")}).encode())).decode())
        return c
    c.login, c.sent = login, sent
    return c


BODY = {"code": "clipper", "plan": "pro", "headline": "50% off your first month"}
AUDIENCE = {"free", "lapsed", "expired", "trial"}


def test_a_dry_run_only_counts(client):
    r = client.login("boss").post("/admin/offers/bulk", json={**BODY, "dry_run": True})
    assert r.status_code == 200 and r.json() == {"ok": True, "count": len(AUDIENCE), "sent": 0}
    assert not any(p.get("offer") for p in PEOPLE.values())
    assert not client.sent


def test_everyone_not_paying_gets_it_live(client):
    r = client.login("boss").post("/admin/offers/bulk", json=BODY)
    assert r.json()["sent"] == len(AUDIENCE)
    have = {uid for uid, p in PEOPLE.items() if p.get("offer")}
    assert have == AUDIENCE
    assert PEOPLE["free"]["offer"]["code"] == "CLIPPER"
    assert {u for e, u in client.sent if e == "offer_changed"} == AUDIENCE


def test_the_popup_reads_it(client):
    client.login("boss").post("/admin/offers/bulk", json=BODY)
    me = client.login("free").get("/me").json()
    assert me["offer"] == {"code": "CLIPPER", "plan": "pro", "headline": "50% off your first month"}


def test_a_resend_shows_it_again_after_not_now(client):
    client.login("boss").post("/admin/offers/bulk", json=BODY)
    client.login("free").post("/offer/dismiss")
    assert client.get("/me").json()["offer"] is None
    client.login("boss").post("/admin/offers/bulk", json=BODY)
    assert client.login("free").get("/me").json()["offer"]["code"] == "CLIPPER"


def test_it_can_be_taken_back(client):
    client.login("boss").post("/admin/offers/bulk", json=BODY)
    client.sent.clear()
    r = client.delete("/admin/offers/bulk?code=CLIPPER")
    assert r.json()["cleared"] == len(AUDIENCE)
    assert not any(p.get("offer") for p in PEOPLE.values())
    assert {u for e, u in client.sent if e == "offer_changed"} == AUDIENCE


def test_only_an_admin(client):
    assert client.login("free").post("/admin/offers/bulk", json=BODY).status_code == 403
    assert client.delete("/admin/offers/bulk?code=CLIPPER").status_code == 403
    assert not any(p.get("offer") for p in PEOPLE.values())


def test_a_bad_code_is_refused(client):
    r = client.login("boss").post("/admin/offers/bulk", json={**BODY, "code": "no spaces!"})
    assert r.status_code == 400


def test_admin_controls_are_wired():
    from src.dashboard import api
    from src.dashboard.aurora_html import DASHBOARD_HTML
    html = api.ADMIN_HTML
    for needle in ('id="bo-send"', 'id="bo-clear"', 'value="CLIPPER"',
                   'value="50% off your first month"', "/admin/offers/bulk"):
        assert needle in html, needle
    assert "msg.event==='offer_changed'" in DASHBOARD_HTML
