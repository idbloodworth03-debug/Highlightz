"""
Membership tiers: plan resolution, price→plan mapping (including the
grandfathered legacy $15 price), and the plan write path.
"""

from src.billing import stripe_billing as sb
from src.billing.plans import get_plan, limits_for, PLAN_LIMITS


def test_get_plan_resolution_rules():
    # Admins and admin-granted trials always get the full experience.
    assert get_plan({"is_admin": True}) == "pro"
    assert get_plan({"subscription_status": "trialing"}) == "pro"
    # Stored plan wins for normal subscribers.
    assert get_plan({"subscription_status": "active", "plan": "starter"}) == "starter"
    assert get_plan({"subscription_status": "active", "plan": "pro"}) == "pro"
    # Legacy $15-era subscribers have no plan field → grandfathered as pro.
    assert get_plan({"subscription_status": "active"}) == "pro"
    # Garbage plan values fall back safely.
    assert get_plan({"plan": "enterprise"}) == "pro"


def test_limits_shape():
    s, p = PLAN_LIMITS["starter"], PLAN_LIMITS["pro"]
    assert (s["max_streams"], s["max_pending"], s["vod"]) == (3, 50, False)
    assert (p["max_streams"], p["max_pending"], p["vod"]) == (10, 200, True)
    assert limits_for({"plan": "starter"})["max_streams"] == 3


def test_plan_for_price_maps_tiers_and_grandfathers_legacy(monkeypatch):
    monkeypatch.setattr(sb.settings, "stripe_price_id_starter", "price_S")
    monkeypatch.setattr(sb.settings, "stripe_price_id_pro", "price_P")
    monkeypatch.setattr(sb.settings, "stripe_price_id", "price_LEGACY15")
    assert sb.plan_for_price("price_S") == "starter"
    assert sb.plan_for_price("price_P") == "pro"
    assert sb.plan_for_price("price_LEGACY15") == "pro"    # grandfathered
    assert sb.plan_for_price("price_unknown") is None      # keep stored plan
    assert sb.plan_for_price(None) is None


def test_extract_price_id_shapes():
    def ev(obj):
        return {"type": "customer.subscription.created", "data": {"object": obj}}
    assert sb.extract_price_id(ev({"items": {"data": [{"price": {"id": "price_A"}}]}})) == "price_A"
    assert sb.extract_price_id(ev({"items": {"data": [{"price": "price_B"}]}})) == "price_B"
    assert sb.extract_price_id(ev({"plan": {"id": "price_C"}})) == "price_C"   # legacy shape
    assert sb.extract_price_id(ev({})) is None
    assert sb.extract_price_id({"type": "checkout.session.completed",
                                "data": {"object": {}}}) is None


def test_set_plan_always_updates(tmp_path, monkeypatch):
    # Unlike promo attribution (first wins), the plan must track the CURRENT
    # subscription — portal upgrades/downgrades take effect via the webhook.
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    u = users.create("carol", "hunter2hunter2")
    users.set_plan(u["id"], "starter")
    assert users.get_by_id(u["id"])["plan"] == "starter"
    users.set_plan(u["id"], "pro")                        # upgrade
    assert users.get_by_id(u["id"])["plan"] == "pro"
