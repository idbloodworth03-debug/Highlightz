"""Invite links: a membership handed over without ever showing a price.

THE PROBLEM THIS SOLVES. Comping someone meant telling them to sign in first so
they'd exist in the admin list — and the sign-in page they landed on said
"From $10/month — cancel anytime" with "Renews monthly" under the button. Being
promised free access and then shown a price above a Connect-your-Twitch prompt
is the exact shape of a scam, so people read it as one. It was also simply
false: the free tier had shipped, the landing page was updated to match, and
the login page never was.

Now the sender hands over a link and says nothing else.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.auth import invites

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(invites, "_INDEX", tmp_path / "invites.json")
    monkeypatch.setattr(invites, "_invites", {})
    monkeypatch.setattr(invites, "_loaded", False)

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "boss", "username": "boss", "is_admin": True,
         "subscription_status": "active", "created_at": now},
        {"id": "guest", "username": "guest", "subscription_status": "none",
         "created_at": now},
        {"id": "other", "username": "other", "subscription_status": "none",
         "created_at": now},
    ]))

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        if uid is None:
            return c
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": uid == "boss", "subscription_status": "none"}).encode())).decode())
        return c

    c.login = login
    c.store = user_store
    c.invites = invites
    c.api = api
    yield c


def _make(client, **kw):
    body = {"plan": "pro", "days": 0, "note": "", "max_uses": 1, "ttl_days": 30}
    body.update(kw)
    r = client.login("boss").post("/admin/invites", json=body)
    assert r.status_code == 201, r.text
    return r.json()


# ── the link itself ──────────────────────────────────────────────────────────

def test_an_invite_grants_the_plan_it_was_created_for(client):
    from src.billing import plans
    inv = _make(client, plan="starter")
    client.api._redeem_invite(inv["code"], client.store.get_by_id("guest"))
    u = client.store.get_by_id("guest")
    assert plans.get_plan(u) == "starter"
    assert u["plan_source"] == "granted", "an invite must not look like a payment"


def test_a_timed_invite_grants_a_window_not_a_permanent_plan(client):
    inv = _make(client, plan="pro", days=14)
    client.api._redeem_invite(inv["code"], client.store.get_by_id("guest"))
    u = client.store.get_by_id("guest")
    assert u["subscription_status"] == "trialing"
    assert u["trial_ends_at"] > time.time()


def test_the_link_never_mentions_billing_and_never_needs_a_session(client):
    """A signed-out stranger clicking it is the entire point. Bouncing them to
    /login — or worse, to the paywall — is what this feature exists to avoid."""
    inv = _make(client)
    r = client.login(None).get(f"/i/{inv['code']}", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/auth/twitch", r.headers["location"]
    assert "billing" not in r.headers["location"]


def test_an_already_signed_in_user_does_not_need_a_second_oauth_trip(client):
    from src.billing import plans
    inv = _make(client, plan="starter")
    r = client.login("guest").get(f"/i/{inv['code']}", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    assert plans.get_plan(client.store.get_by_id("guest")) == "starter"


# ── it is a bearer credential ────────────────────────────────────────────────

def test_an_invite_is_single_use_by_default(client):
    inv = _make(client, plan="pro")
    assert client.api._redeem_invite(inv["code"], client.store.get_by_id("guest"))
    assert not client.api._redeem_invite(inv["code"], client.store.get_by_id("other")), \
        "a single-use link was spent twice"
    assert client.store.get_by_id("other")["subscription_status"] == "none"


def test_reclicking_your_own_link_does_not_burn_a_use_or_fail(client):
    """Browser history, a double tap, the link reopened tomorrow — none of that
    should look like an error or cost the recipient their access."""
    inv = _make(client, plan="pro")
    assert client.api._redeem_invite(inv["code"], client.store.get_by_id("guest"))
    assert client.api._redeem_invite(inv["code"], client.store.get_by_id("guest"))
    live = client.invites.get(inv["code"])
    assert len(live.claims) == 1, "a repeat click spent a second use"


def test_a_multi_use_link_can_be_asked_for_deliberately(client):
    inv = _make(client, plan="starter", max_uses=2)
    assert client.api._redeem_invite(inv["code"], client.store.get_by_id("guest"))
    assert client.api._redeem_invite(inv["code"], client.store.get_by_id("other"))
    assert client.invites.get(inv["code"]).uses_left() == 0


def test_an_expired_link_stops_working(client):
    inv = _make(client, plan="pro")
    live = client.invites.get(inv["code"])
    live.expires_at = time.time() - 1
    assert not client.api._redeem_invite(inv["code"], client.store.get_by_id("guest"))
    assert client.store.get_by_id("guest")["subscription_status"] == "none"


def test_the_link_expiry_is_separate_from_the_membership_duration(client):
    """A 30-day link can grant a permanent plan, and a link good for a week can
    grant a month. Conflating them would make one of the two unexpressible."""
    inv = _make(client, plan="pro", days=0, ttl_days=7)
    assert inv["days"] == 0
    assert 0 < inv["expires_at"] - time.time() <= 7 * 86400 + 5


def test_a_nonsense_code_leaves_the_user_signed_in_rather_than_stranded(client):
    """Never raise out of the OAuth callback. The link failing is recoverable;
    being dumped back at /login after connecting Twitch is what makes someone
    give up entirely."""
    assert client.api._redeem_invite("not-a-real-code", client.store.get_by_id("guest")) is False
    assert client.store.get_by_id("guest")["subscription_status"] == "none"


def test_a_crash_mid_redemption_is_swallowed_rather_than_aborting_the_sign_in(
        client, monkeypatch):
    """The bad-code path above never reaches the try/except — it returns early.
    This one makes the grant itself blow up, which is the case the handler is
    actually wrapped for: the user is mid-OAuth and must still end up signed in.
    """
    from src.auth import users as user_store

    def boom(*a, **k):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(user_store, "grant_plan", boom)
    inv = _make(client, plan="pro")
    assert client.api._redeem_invite(inv["code"], client.store.get_by_id("guest")) is False


def test_an_admin_claim_is_skipped_rather_than_spending_the_link(client):
    inv = _make(client, plan="starter")
    assert client.api._redeem_invite(inv["code"], client.store.get_by_id("boss")) is False
    assert client.invites.get(inv["code"]).uses_left() == 1, \
        "an admin opening the link burned it for its real recipient"


# ── admin surface ────────────────────────────────────────────────────────────

def test_invites_are_listed_with_who_claimed_them(client):
    inv = _make(client, plan="pro", note="for nova")
    client.api._redeem_invite(inv["code"], client.store.get_by_id("guest"))
    rows = client.login("boss").get("/admin/invites").json()["invites"]
    row = next(r for r in rows if r["code"] == inv["code"])
    assert row["note"] == "for nova"
    assert row["uses_left"] == 0 and row["live"] is False
    assert row["claims"][0]["user_id"] == "guest"


def test_revoking_a_link_does_not_take_back_a_membership(client):
    """Revoke kills the LINK. Making it also strip an existing plan would have
    one button quietly do two things."""
    from src.billing import plans
    inv = _make(client, plan="pro")
    client.api._redeem_invite(inv["code"], client.store.get_by_id("guest"))
    assert client.login("boss").delete(f"/admin/invites/{inv['code']}").status_code == 204
    assert client.invites.get(inv["code"]) is None
    assert plans.get_plan(client.store.get_by_id("guest")) == "pro"


def test_an_unknown_plan_cannot_be_minted(client):
    r = client.login("boss").post("/admin/invites", json={"plan": "diamond"})
    assert r.status_code == 400


def test_only_an_admin_can_mint_or_list_invites(client):
    assert client.login("guest").post(
        "/admin/invites", json={"plan": "pro"}).status_code == 403
    assert client.login("guest").get("/admin/invites").status_code == 403


def test_codes_are_unguessable(client):
    """This is a bearer credential for a paid product, not a slug."""
    codes = {_make(client)["code"] for _ in range(5)}
    assert len(codes) == 5
    assert all(len(c) >= 10 for c in codes), codes


# ── the copy that caused this ────────────────────────────────────────────────

def test_the_sign_in_page_does_not_quote_a_price_as_the_headline():
    """It read "From $10/month — cancel anytime" above the Twitch button, with
    "Renews monthly" below it — to someone who had just been told they were
    getting free access, that is a bait-and-switch. It was also false once the
    free tier shipped."""
    from src.dashboard.api import LOGIN_HTML as html
    assert "From $10/month" not in html
    assert "Renews monthly." not in html
    assert "7 days free" in html, \
        "the sign-in page does not say what signing in actually gets you"
    assert "no card required" in html


def test_the_admin_panel_can_actually_mint_a_link():
    """An endpoint with no way to reach it is indistinguishable from not having
    built one."""
    from src.dashboard.api import ADMIN_HTML as html
    assert "/admin/invites" in html
    assert "iv-make" in html and "iv-wrap" in html
    assert "/i/" in html, "the panel never shows the claimable URL"
