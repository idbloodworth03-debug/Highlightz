"""
The affiliate portal.

WHAT IS ACTUALLY AT RISK. This is the first screen in the product built for
somebody who is not the owner and not a customer, and it shows commercial
numbers. Two things therefore matter more than anything else here:

  * an affiliate must never be able to see another affiliate's figures, and
  * the numbers they see must be the same ones the admin page shows for that
    code, or one of the two is lying to somebody about money.

The rest is the shape of a code that people type by hand: unique across
accounts, forgiving about case, and never silently reinterpreted into a
different code than the one that was asked for.
"""

import base64
import json as _j

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.auth import affiliates
from src.dashboard import api


PEOPLE = {
    "boss":   {"id": "boss", "username": "bloodworthhh", "is_admin": True,
               "subscription_status": "active", "plan": "pro"},
    "tommy":  {"id": "tommy", "username": "tommy", "twitch_id": "1",
               "subscription_status": "none", "plan": "free",
               "affiliate_code": "tommy"},
    "andrew": {"id": "andrew", "username": "andrew", "twitch_id": "2",
               "subscription_status": "none", "plan": "free",
               "affiliate_code": "andrewz"},
    "nobody": {"id": "nobody", "username": "nobody", "twitch_id": "3",
               "subscription_status": "none", "plan": "free"},
}

NOW = 1_800_000_000.0
WEEK = 7 * 86400


def _referred(uid, ref, days_ago, paid=False, twitch=True):
    return {"id": uid, "username": uid, "ref": ref,
            "created_at": NOW - days_ago * 86400,
            "twitch_id": "x" if twitch else None,
            "subscription_status": "active" if paid else "none",
            "plan": "pro" if paid else "free"}


@pytest.fixture
def client(monkeypatch):
    from src.auth import users as user_store
    store = dict(PEOPLE)

    monkeypatch.setattr(user_store, "get_by_id", lambda uid: store.get(uid))
    monkeypatch.setattr(user_store, "get_all", lambda: list(store.values()))

    def _set_code(uid, code):
        if uid not in store:
            return False
        if code:
            store[uid] = {**store[uid], "affiliate_code": code}
        else:
            store[uid] = {k: v for k, v in store[uid].items() if k != "affiliate_code"}
        return True
    monkeypatch.setattr(user_store, "set_affiliate_code", _set_code)

    c = TestClient(api.app, base_url="https://testserver")

    def login(uid):
        c.cookies.clear()
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(_j.dumps({
            "auth": True, "user_id": uid,
            "subscription_status": store[uid].get("subscription_status", "none"),
        }).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c

    c.login = login
    c.store = store
    return c


# ── the thing that must never happen ─────────────────────────────────────────

def test_an_affiliate_sees_only_their_own_numbers(client, monkeypatch):
    """No code reaches this endpoint from the request — it is read off the
    signed-in account, so there is no parameter to tamper with."""
    from src.auth import users as user_store
    client.store.update({
        "a1": _referred("a1", "tommy", 20),
        "a2": _referred("a2", "tommy", 20),
        "b1": _referred("b1", "andrewz", 20),
    })
    body = client.login("tommy").get("/portal/stats").json()
    assert body["code"] == "tommy"
    assert body["stats"]["signups"] == 2          # not 3

    body = client.login("andrew").get("/portal/stats").json()
    assert body["code"] == "andrewz"
    assert body["stats"]["signups"] == 1


def test_a_query_parameter_cannot_ask_for_someone_elses_code(client):
    """The obvious attack, and the reason the code is never an input."""
    client.store["a1"] = _referred("a1", "andrewz", 20)
    r = client.login("tommy").get("/portal/stats?code=andrewz")
    assert r.json()["code"] == "tommy"
    assert r.json()["stats"]["code"] == "tommy"


def test_a_signed_out_visitor_cannot_reach_the_portal(client):
    c = TestClient(api.app, base_url="https://testserver", follow_redirects=False)
    assert c.get("/portal").status_code in (302, 401)
    r = c.get("/portal/stats", headers={"accept": "application/json"})
    assert r.status_code == 401


def test_a_non_admin_cannot_list_every_affiliate(client):
    assert client.login("tommy").get("/admin/affiliates").status_code == 403


def test_a_non_admin_cannot_assign_themselves_a_code(client):
    r = client.login("nobody").post("/admin/affiliates/nobody", json={"code": "sneaky"})
    assert r.status_code == 403
    assert "affiliate_code" not in client.store["nobody"]


# ── someone with no code ─────────────────────────────────────────────────────

def test_an_account_without_a_code_gets_a_page_not_an_error(client):
    """They are signed in and legitimate — they just have not been given a
    code yet. A 403 here reads as 'you did something wrong'."""
    c = client.login("nobody")
    assert c.get("/portal").status_code == 200
    body = c.get("/portal/stats").json()
    assert body["code"] is None
    assert "stats" not in body


# ── the numbers ──────────────────────────────────────────────────────────────

def test_the_counts_mean_what_the_page_says_they_mean(client):
    users = [
        _referred("p1", "tommy", 30, paid=True),
        _referred("p2", "tommy", 30),
        _referred("p3", "tommy", 2),                    # too new for retention
        _referred("p4", "tommy", 30, twitch=False),     # never connected
        _referred("x1", "andrewz", 30),                 # somebody else's
    ]
    st = affiliates.stats_for("tommy", users=users,
                              last_active={"p1": NOW - 86400}, now=NOW)
    assert st["signups"] == 4
    assert st["connected"] == 3
    assert st["paid"] == 1
    assert st["last_7"] == 1
    assert st["last_30"] == 4


def test_someone_who_signed_up_yesterday_is_not_counted_as_churned(client):
    """They are excluded from BOTH sides of retention. Counted as churned, a
    good week of fresh signups would make an affiliate's retention appear to
    collapse exactly when they were doing well."""
    users = [_referred("new", "tommy", 1), _referred("old", "tommy", 30)]
    st = affiliates.stats_for("tommy", users=users,
                              last_active={"old": NOW - 3600}, now=NOW)
    assert st["retention_eligible"] == 1
    assert st["retained_wk2"] == 1


def test_an_affiliate_with_no_signups_gets_zeros_not_an_error(client):
    st = affiliates.stats_for("tommy", users=[], now=NOW)
    assert st["signups"] == 0 and st["retention_eligible"] == 0


def test_the_portal_and_the_admin_page_agree(client):
    """One arithmetic, two readers. If these ever diverge, one of them is
    telling somebody the wrong thing about money."""
    client.store.update({"a1": _referred("a1", "tommy", 30, paid=True),
                         "a2": _referred("a2", "tommy", 30)})
    mine = client.login("tommy").get("/portal/stats").json()["stats"]
    rows = client.login("boss").get("/admin/affiliates").json()["rows"]
    theirs = next(r for r in rows if r["code"] == "tommy")
    for k in ("signups", "connected", "paid", "last_7", "last_30", "retained_wk2"):
        assert mine[k] == theirs[k], k


# ── assigning codes ──────────────────────────────────────────────────────────

def test_an_admin_can_assign_and_clear_a_code(client):
    c = client.login("boss")
    r = c.post("/admin/affiliates/nobody", json={"code": "Fresh-Code"})
    assert r.status_code == 200, r.text
    assert client.store["nobody"]["affiliate_code"] == "fresh-code"   # lowercased
    assert c.post("/admin/affiliates/nobody", json={"code": None}).status_code == 200
    assert "affiliate_code" not in client.store["nobody"]


def test_two_accounts_cannot_hold_the_same_code(client):
    """Sharing a code would make the portal a lie for both of them and the
    attribution meaningless."""
    r = client.login("boss").post("/admin/affiliates/nobody", json={"code": "tommy"})
    assert r.status_code == 400
    assert "already belongs" in r.json()["detail"]


def test_reassigning_the_same_code_to_its_own_holder_is_allowed(client):
    """Otherwise the uniqueness check fires against the account's own code and
    re-saving an unchanged record is impossible."""
    r = client.login("boss").post("/admin/affiliates/andrew", json={"code": "andrewz"})
    assert r.status_code == 200, r.text
    assert client.store["andrew"]["affiliate_code"] == "andrewz"


def test_a_built_in_referrer_key_can_be_claimed_and_brings_its_history(client):
    """THE BUG THIS CAUGHT. The first version refused these outright, on the
    theory that a collision would misattribute. It cannot: normalise() resolves
    a built-in key to itself, which is the same string the code stores, so both
    routes land in the same bucket.

    Refusing them broke the exact case the portal exists for. Four referrer
    keys predate this feature and already have signups against them; handing
    the matching person that same code is how they get a portal showing their
    real history instead of starting from zero."""
    from src.auth import referrals
    key = next(iter(referrals.REFERRERS))
    client.store["hist"] = _referred("hist", key, 30)          # a historical signup
    r = client.login("boss").post("/admin/affiliates/nobody", json={"code": key})
    assert r.status_code == 200, r.text
    body = client.login("nobody").get("/portal/stats").json()
    assert body["code"] == key
    assert body["stats"]["signups"] == 1, "their existing history did not follow them"


@pytest.mark.parametrize("bad", ["ab", "x" * 25, "has space", "wow!", "direct",
                                 "admin", "portal", "-- drop", "../etc"])
def test_a_malformed_code_is_refused_rather_than_cleaned_up(client, bad):
    """Silently turning "Tommy's Code!" into "tommyscode" would hand somebody a
    code they never asked for and cannot predict when they print it on a card."""
    r = client.login("boss").post("/admin/affiliates/nobody", json={"code": bad})
    assert r.status_code == 400, bad
    assert "affiliate_code" not in client.store["nobody"]


def test_assigning_to_an_account_that_does_not_exist_is_refused(client):
    r = client.login("boss").post("/admin/affiliates/ghost", json={"code": "ghost"})
    assert r.status_code == 400
    assert "No such account" in r.json()["detail"]


# ── the link actually attributes ─────────────────────────────────────────────

def test_an_assigned_code_resolves_on_a_ref_link(client):
    """Without this the portal counts nothing: normalise() is the ONLY place a
    ?ref= becomes something storable, and a code it does not recognise is a
    link that attributes to nobody."""
    from src.auth import referrals
    assert referrals.normalise("tommy") == "tommy"
    assert referrals.normalise("TOMMY") == "tommy"        # typed off a screenshot
    assert referrals.normalise(" andrewz ") == "andrewz"


def test_an_unassigned_code_still_resolves_to_nothing(client):
    from src.auth import referrals
    assert referrals.normalise("not-a-real-code") is None


def test_the_built_in_referrers_still_win(client):
    from src.auth import referrals
    key = next(iter(referrals.REFERRERS))
    assert referrals.normalise(key) == key


def test_a_broken_lookup_cannot_take_down_the_landing_page(client, monkeypatch):
    """normalise runs on every visit carrying a ref. Losing one attribution is
    recoverable; a 500 on a link somebody just posted is not."""
    from src.auth import referrals
    monkeypatch.setattr(affiliates, "owner_of",
                        lambda c: (_ for _ in ()).throw(RuntimeError("store gone")))
    assert referrals.normalise("anything") is None        # must not raise
