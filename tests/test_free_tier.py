"""The free tier, exercised through the real app rather than through helpers.

The growth plan depends on one thing being true: someone can sign in with no
card and use the product. Before this existed, AuthMiddleware redirected every
non-subscriber to /billing/paywall, so a signup without payment saw nothing at
all. These tests go through the middleware on purpose — asserting on plans.py
alone would not have caught that.
"""

import base64
import json as _j

import pytest


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store

    people = {
        "free_user":  {"id": "free_user", "username": "freddy",
                       "subscription_status": "none"},
        "lapsed":     {"id": "lapsed", "username": "larry",
                       "subscription_status": "inactive", "plan": "pro"},
        "pro_user":   {"id": "pro_user", "username": "patty",
                       "subscription_status": "active", "plan": "pro"},
        "legacy":     {"id": "legacy", "username": "lena",
                       "subscription_status": "active"},   # $15-era, no plan
    }
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(user_store, "get_all", lambda: list(people.values()))

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)

    c = TestClient(api.app)

    def login(uid):
        from itsdangerous import TimestampSigner
        c.cookies.clear()
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(_j.dumps({
            "auth": True, "user_id": uid, "username": people[uid]["username"],
            "subscription_status": people[uid]["subscription_status"],
        }).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c

    c.login = login
    return c


def test_a_user_with_no_subscription_reaches_the_dashboard(client):
    """THE test. This 302'd to /billing/paywall before the free tier."""
    c = client.login("free_user")
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 200, f"free user was bounced: {r.status_code} {r.headers.get('location')}"


def test_a_free_user_can_read_their_own_data(client):
    c = client.login("free_user")
    for path in ("/me", "/clips", "/streams", "/profiles"):
        assert c.get(path).status_code == 200, f"{path} refused a free user"


def test_me_reports_the_free_plan_and_its_limits(client):
    c = client.login("free_user")
    me = c.get("/me").json()
    assert me["plan"] == "free"
    assert me["plan_label"] == "Free"
    assert me["plan_limits"]["max_streams"] == 1
    assert me["plan_limits"]["vod"] is False
    assert me["plan_limits"]["uploads"] is False


def test_free_users_are_still_refused_the_paid_features(client):
    """Open the door, keep the rooms locked. These are the two features that
    cost real CPU and real disk."""
    c = client.login("free_user")
    # A VALID body on purpose: FastAPI validates before the handler runs, so a
    # malformed payload returns 422 and never reaches the plan gate — the test
    # would pass without the gate existing at all.
    r = c.post("/vod/analyze",
               json={"vod_url": "https://www.twitch.tv/videos/123456789"})
    assert r.status_code == 403, f"free user was not refused the VOD scanner: {r.status_code}"
    assert c.get("/uploads").status_code in (403, 503)


def test_a_lapsed_subscriber_lands_on_free_rather_than_a_wall(client):
    c = client.login("lapsed")
    assert c.get("/", follow_redirects=False).status_code == 200
    me = c.get("/me").json()
    assert me["plan"] == "free", "a stored plan outlived the subscription"


def test_a_legacy_subscriber_is_still_grandfathered(client):
    """The $15-era customers have no `plan` field. A pricing change must never
    strip features from someone who already paid for full access."""
    c = client.login("legacy")
    me = c.get("/me").json()
    assert me["plan"] == "pro"
    assert me["plan_limits"]["vod"] is True


def test_a_paying_user_is_unaffected(client):
    c = client.login("pro_user")
    me = c.get("/me").json()
    assert me["plan"] == "pro"
    assert me["plan_limits"]["max_streams"] == 10


def test_being_signed_out_is_still_refused(client):
    """Opening the paywall must not have opened the door."""
    from fastapi.testclient import TestClient
    from src.dashboard import api
    anon = TestClient(api.app)
    r = anon.get("/me", headers={"accept": "application/json"})
    assert r.status_code == 401
