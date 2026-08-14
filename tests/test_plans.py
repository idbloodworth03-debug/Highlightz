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
    # This is the ONLY case where a missing plan means paid, and it requires an
    # active subscription — stripping features from someone who already paid
    # for full access is not an acceptable outcome of a pricing change.
    assert get_plan({"subscription_status": "active"}) == "pro"
    # WITHOUT an active subscription the answer now depends on ONE flag.
    #
    # The free tier was replaced by a self-serve 7-day trial, but every account
    # that existed beforehand was grandfathered and keeps free access. So a
    # lapsed subscriber who predates the cutover still lands on free — honouring
    # the stored plan would hand a former customer Pro forever, and locking out
    # someone who has been using the free tier for months is not something a
    # pricing change may do silently.
    assert get_plan({"plan": "pro", "grandfathered": True}) == "free"
    assert get_plan({"subscription_status": "inactive", "plan": "starter",
                     "grandfathered": True}) == "free"
    # A NEW account in the same state has no free tier to fall back to: its
    # trial ended, so it locks. This is the pair that no amount of reading
    # created_at or subscription_status could separate — hence the flag.
    assert get_plan({"plan": "pro"}) == "locked"
    assert get_plan({"subscription_status": "expired"}) == "locked"
    # Garbage falls to the LEAST access, not to paid — failing open on billing
    # is the expensive direction.
    assert get_plan({"plan": "enterprise"}) == "locked"
    assert get_plan({"plan": "enterprise", "grandfathered": True}) == "free"
    assert get_plan({"subscription_status": "active", "plan": "enterprise"}) == "pro"
    # No user in hand is the least access, not the free tier — a deleted account
    # still holding a session must not keep a stream slot.
    assert get_plan(None) == "locked"
    assert get_plan({}) == "locked"
    # Labelers are the training team and need the real product unpaid.
    assert get_plan({"is_labeler": True}) == "pro"


def test_limits_shape():
    f, s, p = PLAN_LIMITS["free"], PLAN_LIMITS["starter"], PLAN_LIMITS["pro"]
    assert (f["max_streams"], f["max_pending"], f["vod"], f["uploads"]) == (1, 15, False, False)
    assert (s["max_streams"], s["max_pending"], s["vod"]) == (3, 50, False)
    assert (p["max_streams"], p["max_pending"], p["vod"]) == (10, 200, True)
    assert limits_for({"subscription_status": "active", "plan": "starter"})["max_streams"] == 3
    # An empty/unknown user is LOCKED, not free — see get_plan. A deleted
    # account holding a live session must not keep a stream slot.
    assert limits_for({})["max_streams"] == 0
    assert limits_for({"grandfathered": True})["max_streams"] == 1


def test_free_is_one_stream_because_a_stream_is_the_scarce_resource():
    """Every monitored stream runs a streamlink+ffmpeg audio meter on ONE
    shared vCPU. That is the constraint the whole free tier has to respect —
    raising this is a capacity decision, not a config tweak."""
    assert PLAN_LIMITS["free"]["max_streams"] == 1
    assert PLAN_LIMITS["free"]["price"] == 0


def test_free_users_get_no_disk_heavy_features():
    """VOD scanning and the Clip Editor are the two features that cost real
    CPU and real disk on a 50 GB box shared with clipping and billing."""
    assert PLAN_LIMITS["free"]["vod"] is False
    assert PLAN_LIMITS["free"]["uploads"] is False


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


# ── free tier access ─────────────────────────────────────────────────────────

def test_signing_in_without_a_subscription_is_no_longer_a_paywall():
    """Before the free tier, AuthMiddleware redirected anyone without an active
    subscription to /billing/paywall — a signup with no card saw no product at
    all. The whole growth plan depends on that not being true, so the redirect
    must not come back."""
    import inspect
    from src.dashboard import api
    # Strip comments: the middleware explains what it used to do and names the
    # paywall in prose, which is not the same as redirecting to it.
    src = "\n".join(l for l in inspect.getsource(api.AuthMiddleware.dispatch).splitlines()
                    if not l.strip().startswith("#"))
    assert "/billing/paywall" not in src, "the paywall redirect is back in the middleware"
    assert "402" not in src, "non-subscribers are being refused at the middleware again"


def test_realtime_is_not_a_paid_feature():
    """Closing the socket on a free user leaves them with a dashboard that
    silently stops updating — which reads as broken, not as a limit. What free
    users get less OF is enforced at the limits."""
    import inspect
    from src.dashboard import api
    src = "\n".join(l for l in inspect.getsource(api.websocket_endpoint).splitlines()
                    if not l.strip().startswith("#"))
    assert 'not in ("active", "trialing")' not in src, \
        "the websocket is refusing non-subscribers again"


def test_a_lapsed_subscriber_keeps_the_free_allowance():
    # NOTE: "keeps" now means "if they predate the trial cutover". A lapsed
    # subscriber who signed up AFTER it has no free tier to fall back to and
    # lands on `locked` — covered in test_get_plan_resolution_rules.
    """They drop to free, not out. Stopping every stream would take away the
    one a free user is entitled to."""
    from src.billing.plans import limits_for
    lapsed = {"subscription_status": "inactive", "plan": "pro", "grandfathered": True}
    assert limits_for(lapsed)["max_streams"] == 1


def test_the_stream_limit_trim_keeps_the_oldest_streams():
    """Newest-first is deliberate: the channel someone added first is the one
    they care most about, and it is the one still running afterwards."""
    import asyncio
    from src.dashboard import api

    api._streams.clear()
    for i, ch in enumerate(["first", "second", "third"]):
        api._streams[f"u1:{ch}"] = {"channel": ch, "user_id": "u1", "added_at": float(i)}
    api._streams["other:keep"] = {"channel": "keep", "user_id": "other", "added_at": 0.0}

    import src.auth.users as us
    real_get, real_save = us.get_by_id, api._save_streams
    real_pub = api._publish_remove_stream
    try:
        # A grandfathered legacy account: free tier, 1 stream. Without the flag
        # this user would be `locked` (0 streams) and ALL THREE would stop,
        # which is a different test.
        us.get_by_id = lambda uid: {"subscription_status": "none",
                                    "grandfathered": True}   # free: 1 stream
        api._save_streams = lambda: None
        api._publish_remove_stream = None
        stopped = asyncio.run(api._enforce_stream_limit("u1"))
    finally:
        us.get_by_id, api._save_streams = real_get, real_save
        api._publish_remove_stream = real_pub

    assert stopped == 2
    assert "u1:first" in api._streams, "trimmed the oldest instead of the newest"
    assert "u1:second" not in api._streams and "u1:third" not in api._streams
    assert "other:keep" in api._streams, "trimmed another user's stream"
    api._streams.clear()
