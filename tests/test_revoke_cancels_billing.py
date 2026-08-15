"""Revoking access has to stop the billing that paid for it.

Revoke used to touch only our own records, so a revoked user lost the product
and kept being charged for it every month. That is the worst possible pairing —
they cannot use it, they are still paying, and the first anyone hears about it
is a chargeback.

The admin DELETE path was worse still: stripe_customer_id is the only handle we
have on someone's subscription, and deleting the user destroys it. A delete that
skipped the cancel left a customer being charged forever with nothing on our
side left to find them by.
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

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_save_streams", lambda: None)

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)
    monkeypatch.setattr(api, "_stop_user_streams_now", _noop)

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "boss", "username": "boss", "is_admin": True,
         "subscription_status": "active", "created_at": now},
        {"id": "payer", "username": "payer", "subscription_status": "active",
         "plan": "pro", "stripe_customer_id": "cus_PAYER", "created_at": now},
        {"id": "comped", "username": "comped", "subscription_status": "active",
         "plan": "pro", "plan_source": "granted", "created_at": now},
    ]))

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "boss", "username": "boss",
         "is_admin": True, "subscription_status": "active"}).encode())).decode())
    c.api = api
    return c


def _stub_cancel(monkeypatch, result):
    """Replace the Stripe call and record who it was asked about."""
    from src.billing import stripe_billing
    seen = []

    async def _cancel(customer_id):
        seen.append(customer_id)
        if isinstance(result, Exception):
            raise result
        return result
    monkeypatch.setattr(stripe_billing, "cancel_customer_subscriptions", _cancel)
    return seen


# ── revoke ───────────────────────────────────────────────────────────────────

def test_revoking_a_paying_user_cancels_their_subscription(client, monkeypatch):
    seen = _stub_cancel(monkeypatch, 1)
    r = client.post("/admin/users/payer/revoke")
    assert r.status_code == 200
    assert seen == ["cus_PAYER"], "Stripe was never asked to cancel"
    assert r.json()["stripe_cancelled"] == 1
    assert r.json()["stripe_ok"] is True


def test_access_is_still_revoked_when_stripe_is_down(client, monkeypatch):
    """Removing access is the admin's actual intent. It must not become
    contingent on a third party being reachable."""
    _stub_cancel(monkeypatch, None)          # None = could not reach Stripe
    r = client.post("/admin/users/payer/revoke")
    assert r.status_code == 200

    from src.auth import users as user_store
    assert user_store.get_by_id("payer")["subscription_status"] == "inactive"


def test_a_failed_cancel_is_reported_rather_than_swallowed(client, monkeypatch):
    """The admin has to know billing may still be running. Reporting success
    here is how someone walks away believing the money stopped."""
    _stub_cancel(monkeypatch, None)
    body = client.post("/admin/users/payer/revoke").json()
    assert body["stripe_ok"] is False
    assert body["stripe_cancelled"] is None


def test_nothing_to_cancel_is_not_reported_as_a_failure(client, monkeypatch):
    """A comped user has no Stripe customer at all. That is a clean revoke, not
    a broken one — conflating the two would cry wolf on every comp."""
    seen = _stub_cancel(monkeypatch, 0)
    body = client.post("/admin/users/comped/revoke").json()
    assert seen == [], "Stripe was called for a user with no customer id"
    assert body["stripe_ok"] is True and body["stripe_cancelled"] == 0


def test_the_stripe_customer_id_survives_a_revoke(client, monkeypatch):
    """update_subscription is called with customer_id=None. If that ever wiped
    the stored id, a failed cancel could never be retried — the handle would be
    gone."""
    _stub_cancel(monkeypatch, None)
    client.post("/admin/users/payer/revoke")
    from src.auth import users as user_store
    assert user_store.get_by_id("payer")["stripe_customer_id"] == "cus_PAYER"


# ── delete ───────────────────────────────────────────────────────────────────

def test_deleting_a_user_cancels_their_subscription_first(client, monkeypatch):
    """Ordering is the whole test. user_store.delete destroys the customer id,
    so a cancel attempted afterwards has nothing to work with."""
    seen = _stub_cancel(monkeypatch, 1)
    r = client.request("DELETE", "/admin/users/payer")
    assert r.status_code == 200
    assert seen == ["cus_PAYER"], "the account was deleted without stopping billing"

    from src.auth import users as user_store
    assert user_store.get_by_id("payer") is None, "the user was not actually deleted"


def test_a_stripe_outage_does_not_block_deleting_an_account(client, monkeypatch):
    """Refusing to delete would leave an admin unable to remove an account
    because a third party is down — and unable to honour a deletion request."""
    _stub_cancel(monkeypatch, None)
    r = client.request("DELETE", "/admin/users/payer")
    assert r.status_code == 200
    assert r.json()["stripe_ok"] is False
    from src.auth import users as user_store
    assert user_store.get_by_id("payer") is None


# ── the helper's contract ────────────────────────────────────────────────────

def test_the_helper_distinguishes_no_subscriptions_from_no_answer():
    """0 and None are opposite facts: one means the customer is not being
    charged, the other means they might be and we do not know. Returning 0 for
    both is what made the failure invisible."""
    import inspect
    from src.billing import stripe_billing
    src = inspect.getsource(stripe_billing.cancel_customer_subscriptions)
    assert "-> int | None" in src
    assert "return None" in src, "a Stripe error still reports as 'nothing to cancel'"


def test_the_admin_ui_does_not_report_a_failed_cancel_as_success():
    from src.dashboard.api import ADMIN_HTML as html
    i = html.index("u-revoke')){")
    block = html[i:i + 900]
    assert "stripe_ok === false" in block, "the UI never checks whether billing actually stopped"
    assert "FAILED" in block
