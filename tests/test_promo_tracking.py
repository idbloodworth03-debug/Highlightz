"""
Promo-code signup attribution: extract the promotion-code id from Stripe
subscription events (legacy `discount` and newer `discounts` shapes), resolve
it to the human code, and record it once per user — payouts for the streamer
partnership key off this, so first attribution must win.
"""

import asyncio
from unittest.mock import MagicMock, patch

from src.billing import stripe_billing as sb


def _event(obj):
    return {"type": "customer.subscription.created", "data": {"object": obj}}


def test_extract_promo_id_handles_all_shapes():
    # Legacy single `discount` with a plain id
    assert sb.extract_promo_id(_event({"discount": {"promotion_code": "promo_A"}})) == "promo_A"
    # Legacy `discount` with an expanded object
    assert sb.extract_promo_id(_event({"discount": {"promotion_code": {"id": "promo_B"}}})) == "promo_B"
    # Newer `discounts` list
    assert sb.extract_promo_id(_event({"discounts": [{"promotion_code": "promo_C"}]})) == "promo_C"
    # No discount at all
    assert sb.extract_promo_id(_event({})) is None
    assert sb.extract_promo_id(_event({"discount": None, "discounts": []})) is None
    # Non-subscription events never match
    assert sb.extract_promo_id({"type": "checkout.session.completed",
                                "data": {"object": {"discount": {"promotion_code": "promo_X"}}}}) is None


def test_resolve_promo_code_caches(monkeypatch):
    monkeypatch.setattr(sb.settings, "stripe_secret_key", "sk_test")
    sb._promo_code_cache.clear()
    fake_client = MagicMock()
    fake_client.promotion_codes.retrieve.return_value = {"code": "NOVA50"}
    with patch.object(sb, "_client", return_value=fake_client):
        assert asyncio.run(sb.resolve_promo_code("promo_1")) == "NOVA50"
        assert asyncio.run(sb.resolve_promo_code("promo_1")) == "NOVA50"
    fake_client.promotion_codes.retrieve.assert_called_once()   # second hit = cache
    # Errors fail soft — attribution must never break the webhook.
    sb._promo_code_cache.clear()
    with patch.object(sb, "_client", side_effect=RuntimeError("boom")):
        assert asyncio.run(sb.resolve_promo_code("promo_2")) is None


def test_set_promo_code_first_attribution_wins(tmp_path, monkeypatch):
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    u = users.create("alice", "hunter2hunter2")
    users.set_promo_code(u["id"], "NOVA50")
    assert users.get_by_id(u["id"])["promo_code"] == "NOVA50"
    # A later re-subscribe with a different code must not rewrite the referrer.
    users.set_promo_code(u["id"], "OTHER10")
    assert users.get_by_id(u["id"])["promo_code"] == "NOVA50"
    # Empty code is a no-op, unknown user doesn't crash.
    users.set_promo_code(u["id"], "")
    users.set_promo_code("missing", "X")
