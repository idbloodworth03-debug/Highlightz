"""Upgrade prompts offer Starter where Starter solves the problem (owner,
2026-09-30: "fix the starter upgrade prompts").

Pro-only features (VOD scanner, Clip Editor, Scheduler, Autopilot) rightly say
"Upgrade to Pro": Starter does not have them. A LIMIT that Starter raises must
name Starter to a free user. What these defend:
  1. the weekly keep-limit refusal names Starter and Pro to a free user, Pro to a
     Starter user, and nothing to Pro, with numbers read from PLAN_LIMITS;
  2. at the weekly wall, and on a comped trial's Subscribe, the link is the plans
     page (both plans), not a straight-to-Pro checkout;
  3. the limits already fixed stay fixed: stream limit and full queue.
"""

import pytest


@pytest.mark.parametrize("plan_user,expect,absent", [
    ({"id": "u", "subscription_status": "none"}, ["Starter ($10/mo) keeps 100 a week", "Pro ($25/mo) keeps as many as you like"], []),
    ({"id": "u", "subscription_status": "active", "plan": "starter"}, ["Pro ($25/mo) keeps as many as you like"], ["Starter"]),
    ({"id": "u", "subscription_status": "active", "plan": "pro"}, [], ["Starter", "Pro"]),
])
def test_the_weekly_limit_names_the_next_plan(monkeypatch, plan_user, expect, absent):
    from src.dashboard import api
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: plan_user)
    hint = api._library_upgrade_hint("u")
    for e in expect:
        assert e in hint
    for a in absent:
        assert a not in hint


def test_a_free_user_at_the_weekly_wall_is_told_about_starter(monkeypatch):
    import base64, json
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    from src.dashboard import api
    from src.auth import users as user_store
    me = {"id": "u", "username": "u", "subscription_status": "none"}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: me)
    monkeypatch.setattr(user_store, "_load", lambda: [me])
    monkeypatch.setattr(api, "_clips", {"c1": {"id": "c1", "user_id": "u", "status": "pending", "channel": "x"}})
    monkeypatch.setattr(api, "library_room", lambda uid: (30, 30))
    c = TestClient(api.app, base_url="https://testserver")
    c.cookies.set("session", TimestampSigner(api.settings.dashboard_secret_key).sign(base64.b64encode(json.dumps(
        {"auth": True, "user_id": "u", "username": "u", "subscription_status": "none"}).encode())).decode())
    r = c.post("/clips/c1/approve")
    assert r.status_code == 403
    assert "Starter ($10/mo) keeps 100 a week" in r.json()["detail"]
    assert api._clips["c1"]["status"] == "pending"


def test_the_walls_link_to_the_plans_page():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "libCapped && libLeft <= 0 &&" in page and 'href="/billing/paywall"' in page
    assert "me.trial_converts?'/billing/portal':'/billing/paywall'" in page


def test_the_limits_already_fixed_stay_fixed():
    from src.dashboard import api
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    src = open(api.__file__).read()
    assert '"free": " Starter ($10/mo) gives you 3 streams, Pro gives 10."' in src
    assert "(nextPlan === 'starter' ? 'Starter' : 'Pro') + ' holds '" in page
