"""
Duplicate-signup guard: the same billing email must never pay for two
accounts. Detection happens at the webhook (Twitch login carries no email, so
checkout can't pre-validate); remediation is REFUND FIRST, then cancel — if
the refund fails the subscription is left alone, because a customer who paid
and lost access is the one unacceptable outcome.
"""

import asyncio
from unittest.mock import MagicMock, patch

from src.billing import stripe_billing as sb


def test_find_other_active_with_email(tmp_path, monkeypatch):
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    a = users.create("alice", "hunter2hunter2")
    b = users.create("bob", "hunter2hunter2")
    users.set_email(a["id"], "  Same@Email.COM ")          # normalized on write
    users.update_subscription(a["id"], "cus_A", "active")

    # Bob signing up with the same email (any case) is a duplicate of Alice.
    dup = users.find_other_active_with_email("same@email.com", b["id"])
    assert dup and dup["id"] == a["id"]
    # Alice checking her own email is NOT a duplicate of herself.
    assert users.find_other_active_with_email("same@email.com", a["id"]) is None
    # Not-active states on the other account don't count as a paid duplicate.
    users.update_subscription(a["id"], "cus_A", "trialing")
    assert users.find_other_active_with_email("same@email.com", b["id"]) is None
    users.update_subscription(a["id"], "cus_A", "inactive")
    assert users.find_other_active_with_email("same@email.com", b["id"]) is None


def _client_with_invoice(amount_paid, pi="pi_1"):
    c = MagicMock()
    c.invoices.retrieve.return_value = {"amount_paid": amount_paid,
                                        "payment_intent": pi, "charge": None}
    return c


def test_remediation_refunds_then_cancels(monkeypatch):
    monkeypatch.setattr(sb.settings, "stripe_secret_key", "sk_test")
    client = _client_with_invoice(1000)
    with patch.object(sb, "_client", return_value=client):
        ok = asyncio.run(sb.refund_and_cancel_subscription(
            {"id": "sub_1", "latest_invoice": "in_1"}))
    assert ok
    client.refunds.create.assert_called_once_with(params={"payment_intent": "pi_1"})
    client.subscriptions.cancel.assert_called_once_with("sub_1")


def test_remediation_aborts_when_refund_fails(monkeypatch):
    # Refund failed → the subscription must NOT be cancelled (access stays,
    # case is logged for manual handling).
    monkeypatch.setattr(sb.settings, "stripe_secret_key", "sk_test")
    client = _client_with_invoice(1000)
    client.refunds.create.side_effect = RuntimeError("card network says no")
    with patch.object(sb, "_client", return_value=client):
        ok = asyncio.run(sb.refund_and_cancel_subscription(
            {"id": "sub_1", "latest_invoice": "in_1"}))
    assert ok is False
    client.subscriptions.cancel.assert_not_called()


def test_remediation_skips_refund_for_free_first_invoice(monkeypatch):
    # A 100%-off promo makes the first invoice $0 — nothing to refund, but the
    # duplicate subscription still gets cancelled before it ever charges.
    monkeypatch.setattr(sb.settings, "stripe_secret_key", "sk_test")
    client = _client_with_invoice(0)
    with patch.object(sb, "_client", return_value=client):
        ok = asyncio.run(sb.refund_and_cancel_subscription(
            {"id": "sub_1", "latest_invoice": "in_1"}))
    assert ok
    client.refunds.create.assert_not_called()
    client.subscriptions.cancel.assert_called_once_with("sub_1")


def test_customer_email_fails_open(monkeypatch):
    monkeypatch.setattr(sb.settings, "stripe_secret_key", "sk_test")
    with patch.object(sb, "_client", side_effect=RuntimeError("boom")):
        assert asyncio.run(sb.customer_email("cus_A")) is None
    client = MagicMock()
    client.customers.retrieve.return_value = {"email": "  User@X.com "}
    with patch.object(sb, "_client", return_value=client):
        assert asyncio.run(sb.customer_email("cus_A")) == "user@x.com"
