"""Studio, Starter off the site, Pro "Most popular" (owner, 2026-11-03).

"lets remove the 10$ tier from the site. Lets add a new tier that is 250$ this
needs to be better than the highest tier ... make the 25$ highlighted as like
'best seller' or 'popular'". The owner chose the name (Studio) and all four
perks: front of the line, more channels and limits, clip videos kept 30 days,
no campaign fee + direct support.

What these pin:
  1. the plan: better than Pro on every number and flag; Starter not sold but
     its subscribers keep it; upgrade prompts point at a tier on sale;
  2. billing: checkout and the in-place switch sell Pro and Studio only, a
     missing Studio price is a friendly page not an error, the webhook and the
     MRR know Studio;
  3. each perk does what the pricing says: reserved live slots, clips and
     renders first, videos never trimmed early, campaigns without a fee;
  4. every pricing surface shows Free / Pro / Studio, Pro marked "Most
     popular", and no Starter or $10 for sale.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time

import pytest
from itsdangerous import TimestampSigner

from src.billing import plans


# ── 1. the plan ──────────────────────────────────────────────────────────────

def test_studio_is_better_than_pro_on_everything():
    pro, sd = plans.PLAN_LIMITS["pro"], plans.PLAN_LIMITS["studio"]
    assert sd["price"] == 250 and pro["price"] == 25
    for k in ("max_streams", "max_pending", "max_suggested", "max_library_week"):
        assert sd[k] >= pro[k], k
    assert sd["max_streams"] > pro["max_streams"] and sd["max_pending"] > pro["max_pending"]
    for k in ("vod", "uploads"):
        assert sd[k] and pro[k]
    for k in ("priority", "keep_files", "no_campaign_fee", "direct_support"):
        assert sd[k] is True and pro[k] is False, k
    assert sd["pauses_when_idle"] is False


def test_starter_is_not_sold_but_its_subscribers_keep_it():
    assert plans.SELLABLE_PLANS == ("pro", "studio")
    assert "starter" in plans.PAID_PLANS and "studio" in plans.PAID_PLANS
    u = {"subscription_status": "active", "plan": "starter"}
    assert plans.get_plan(u) == "starter"
    assert plans.limits_for(u)["max_streams"] == 3
    assert plans.billed_monthly(u) == 10.0
    assert plans.billed_monthly({"subscription_status": "active", "plan": "studio"}) == 250.0


def test_studio_resolves_from_a_subscription_a_trial_or_a_grant():
    assert plans.get_plan({"subscription_status": "active", "plan": "studio"}) == "studio"
    trial = {"subscription_status": "trialing", "plan": "studio",
             "trial_ends_at": time.time() + 86400}
    assert plans.get_plan(trial) == "studio"
    assert plans.limits_for(trial)["priority"] is True


def test_upgrade_prompts_point_at_a_tier_on_sale():
    assert plans.next_plan("free") == "pro" and plans.next_plan("locked") == "pro"
    assert plans.next_plan("starter") == "pro" and plans.next_plan("pro") == "studio"
    assert plans.next_plan("studio") is None
    assert plans.POPULAR_PLAN == "pro"


# ── 2. billing ───────────────────────────────────────────────────────────────

@pytest.fixture
def world(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.auth import users as user_store
    from src.billing import stripe_billing
    from src.dashboard import api

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api.settings, "stripe_secret_key", "sk_test")
    monkeypatch.setattr(api.settings, "stripe_price_id_starter", "price_starter")
    monkeypatch.setattr(api.settings, "stripe_price_id_pro", "price_pro")
    monkeypatch.setattr(api.settings, "stripe_price_id_studio", "price_studio")
    now = time.time()
    (tmp_path / "users.json").write_text(json.dumps([
        {"id": "free1", "username": "free1", "subscription_status": "none", "created_at": now},
        {"id": "pro1", "username": "pro1", "subscription_status": "active", "plan": "pro",
         "stripe_customer_id": "cus_P", "created_at": now},
        {"id": "st1", "username": "st1", "subscription_status": "active", "plan": "starter",
         "stripe_customer_id": "cus_S", "created_at": now},
    ]))
    checkouts, changes = [], []

    async def _checkout(uid, username, price_id, **kw):
        checkouts.append(price_id)
        return "https://checkout.stripe.com/c/test"
    monkeypatch.setattr(stripe_billing, "create_checkout_url", _checkout)

    async def _change(customer_id, price_id):
        changes.append((customer_id, price_id))
        return "sub_1"
    monkeypatch.setattr(stripe_billing, "change_subscription_price", _change)

    async def _bcast(msg, user_id=None):
        pass
    monkeypatch.setattr(api, "broadcast", _bcast)

    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def as_user(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": False,
             "subscription_status": "active"}).encode())).decode())
        return c
    c.as_user, c.checkouts, c.changes, c.api, c.store = as_user, checkouts, changes, api, user_store
    return c


def test_studio_checkout_uses_the_studio_price(world):
    r = world.as_user("free1").get("/billing/checkout?plan=studio", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"].startswith("https://checkout.stripe.com/")
    assert world.checkouts == ["price_studio"]


def test_starter_can_no_longer_be_bought(world):
    r = world.as_user("free1").get("/billing/checkout?plan=starter", follow_redirects=False)
    assert r.headers["location"] == "/billing/paywall" and world.checkouts == []
    r = world.as_user("pro1").get("/billing/change?plan=starter", follow_redirects=False)
    assert r.headers["location"] == "/billing/paywall" and world.changes == []


def test_a_missing_studio_price_is_a_friendly_page_not_an_error(world, monkeypatch):
    monkeypatch.setattr(world.api.settings, "stripe_price_id_studio", "")
    r = world.as_user("free1").get("/billing/checkout?plan=studio", follow_redirects=False)
    assert r.headers["location"] == "/billing/paywall?unavailable=studio"
    page = world.as_user("free1").get("/billing/paywall?unavailable=studio").text
    assert "Studio is not open for checkout yet" in page and world.checkouts == []


def test_pro_upgrades_to_studio_only_after_confirming(world):
    c = world.as_user("pro1")
    page = c.get("/billing/change?plan=studio").text
    assert "Switch to Studio?" in page and "$250/month" in page and world.changes == []
    token = re.search(r'name="token" value="([^"]+)"', page).group(1)
    r = c.post("/billing/change", data={"plan": "studio", "token": token},
               follow_redirects=False)
    assert r.status_code == 303 and world.changes == [("cus_P", "price_studio")]
    assert world.store.get_by_id("pro1")["plan"] == "studio"


def test_a_starter_subscriber_can_still_move_up_to_pro(world):
    page = world.as_user("st1").get("/billing/change?plan=pro").text
    assert "Switch to Pro?" in page


def test_the_webhook_and_scripts_know_the_studio_price(monkeypatch):
    from src.billing import stripe_billing
    monkeypatch.setattr(stripe_billing.settings, "stripe_price_id_studio", "price_studio")
    assert stripe_billing.plan_for_price("price_studio") == "studio"
    assert stripe_billing.price_id_for("studio") == "price_studio"


def test_offers_can_be_for_studio_but_never_starter(world):
    o = world.store.set_offer("free1", "SAVE50", "studio", "50% off")
    assert o and world.store.get_by_id("free1")["offer"]["plan"] == "studio"
    world.store.set_offer("free1", "SAVE50", "starter", "50% off")
    assert world.store.get_by_id("free1")["offer"]["plan"] == "pro"


# ── 3. the perks ─────────────────────────────────────────────────────────────

@pytest.fixture
def slots(monkeypatch):
    from src.dashboard import api
    monkeypatch.setattr(api, "_live_slots", set())
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 4)
    studio_users = {"sd"}
    monkeypatch.setattr(plans, "uid_has_priority", lambda uid: uid in studio_users)
    return api


def test_with_no_studio_channels_nobody_loses_a_slot(slots):
    for i in range(4):
        assert slots.acquire_live_slot(f"u{i}:ch{i}")
    assert not slots.acquire_live_slot("u9:ch9")


def test_studio_channels_keep_slots_held_back_for_them(slots):
    slots._streams.update({"sd:a": {"user_id": "sd", "channel": "a", "status": "starting"},
                           "sd:b": {"user_id": "sd", "channel": "b", "status": "starting"}})
    assert slots.acquire_live_slot("u1:x") and slots.acquire_live_slot("u2:y")
    assert not slots.acquire_live_slot("u3:z"), "a normal channel took a reserved slot"
    assert slots.acquire_live_slot("sd:a") and slots.acquire_live_slot("sd:b")
    assert not slots.acquire_live_slot("sd:c"), "past the hardware cap"


def test_the_reserve_never_takes_the_last_slot_from_everyone(slots, monkeypatch):
    monkeypatch.setattr(slots.settings, "max_concurrent_streams", 1)
    slots._streams.update({"sd:a": {"user_id": "sd", "channel": "a", "status": "starting"}})
    assert slots.acquire_live_slot("u1:x")


def test_paused_studio_channels_hold_nothing_back(slots):
    slots._streams.update({"sd:a": {"user_id": "sd", "channel": "a", "status": "paused"}})
    for i in range(4):
        assert slots.acquire_live_slot(f"u{i}:ch{i}")


class _FakeRedis:
    def __init__(self):
        self.lists = {}

    async def rpush(self, key, v):
        self.lists.setdefault(key, []).append(v)

    async def blpop(self, keys, timeout=0):
        for k in keys:
            if self.lists.get(k):
                return k, self.lists[k].pop(0)
        return None

    async def llen(self, key):
        return len(self.lists.get(key, []))


def test_studio_clips_are_cut_first_and_in_order():
    from src.queue.job_queue import ClipJob, JobQueue

    async def go():
        q = JobQueue(priority_of=lambda uid: uid == "sd")
        q._redis = _FakeRedis()
        for uid, ch in (("a", "one"), ("sd", "two"), ("b", "three"), ("sd", "four")):
            await q.push(ClipJob(channel=ch, platform="twitch", trigger_score=1.0,
                                 trigger_signals=[], chat_snapshot=[], user_id=uid))
        assert await q.queue_length() == 4
        return [(await q.pop()).channel for _ in range(4)]
    assert asyncio.run(go()) == ["two", "four", "one", "three"]


def test_a_studio_render_goes_next():
    from src.autopilot import render

    async def go():
        slot = render._PrioritySlot()
        order = []
        await slot.acquire()

        async def wait(name, prio):
            async with slot.hold(prio):
                order.append(name)
        tasks = [asyncio.create_task(wait("normal", False))]
        await asyncio.sleep(0)
        tasks.append(asyncio.create_task(wait("studio", True)))
        await asyncio.sleep(0)
        slot.release()
        await asyncio.gather(*tasks)
        return order, slot._busy
    order, busy = asyncio.run(go())
    assert order == ["studio", "normal"] and busy is False


def test_a_cancelled_wait_never_wedges_the_render_slot():
    from src.autopilot import render

    async def go():
        slot = render._PrioritySlot()
        await slot.acquire()
        t = asyncio.create_task(slot.acquire(True))
        await asyncio.sleep(0)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)
        slot.release()
        await asyncio.wait_for(slot.acquire(), 1)     # would hang if wedged
        slot.release()
        return slot._busy
    assert asyncio.run(go()) is False


def test_studio_videos_are_never_trimmed_early(tmp_path, monkeypatch):
    from src.clips import files
    monkeypatch.setattr(files, "_ROOT", tmp_path)
    monkeypatch.setattr(files.settings, "clip_file_max_total_mb", 1)
    old = time.time() - 7200
    for name in ("studio1", "free1", "free2"):
        p = tmp_path / f"{name}.mp4"
        p.write_bytes(b"x" * 600_000)
        os.utime(p, (old, old))
    files._forget_scan()
    files.trim_to_cap(keep_ids=set(), exempt_ids={"studio1"})
    assert (tmp_path / "studio1.mp4").exists()
    assert not (tmp_path / "free1.mp4").exists()


def test_studio_campaigns_carry_no_fee(monkeypatch):
    from src.campaigns import requests as creq
    monkeypatch.setattr(creq.settings, "campaign_flat_fee_usd", 10.0)
    monkeypatch.setattr(creq.settings, "campaign_fee_pct", 5.0)
    studio = {"id": "s", "subscription_status": "active", "plan": "studio"}
    pro = {"id": "p", "subscription_status": "active", "plan": "pro"}
    assert creq.quote(500, creq.fee_waived(studio))["total"] == 500.0
    assert creq.quote(500, creq.fee_waived(pro))["total"] == 535.0
    p = creq.pricing(studio)
    assert p["fee_waived"] and p["flat_fee"] == 0 and p["fee_pct"] == 0
    assert creq.pricing(pro)["fee_pct"] == 5.0 and creq.pricing()["fee_waived"] is False


# ── 4. every pricing surface ─────────────────────────────────────────────────

def _plans_section(html: str) -> str:
    return html[html.index('id="pricing"'):html.index('id="faq"')]


def test_the_landing_page_sells_free_pro_and_studio():
    from src.dashboard.api import LANDING_HTML
    sec = _plans_section(LANDING_HTML)
    names = re.findall(r'<h3 class="plan-name">([^<]+)</h3>', sec)
    assert names == ["Free", "Pro", "Studio"]
    assert "Starter" not in sec and "$10<" not in sec
    pop = sec[sec.index('class="plan is-pop"'):]
    assert pop.index("Most popular") < pop.index('plan-name">Pro')
    assert sec.count("Most popular") == 1 and "$250" in sec
    assert '"highPrice": "250.00"' in LANDING_HTML and "Starter $10" not in LANDING_HTML


def test_the_upgrade_page_offers_pro_and_studio_only():
    from src.dashboard.api import PAYWALL_HTML
    assert "plan=studio" in PAYWALL_HTML and "plan=pro" in PAYWALL_HTML
    assert "plan=starter" not in PAYWALL_HTML and "Choose Starter" not in PAYWALL_HTML
    assert PAYWALL_HTML.count("Most popular") == 1
    assert "from $10" not in PAYWALL_HTML


def test_ai_summaries_terms_and_faq_name_studio_not_starter():
    from fastapi.testclient import TestClient
    from src.dashboard import api
    c = TestClient(api.app, base_url="https://testserver")
    for path in ("/llms.txt", "/llms-full.txt"):
        body = c.get(path).text
        assert "Studio" in body and "$250" in body and "Starter —" not in body, path
    assert "Studio is $250" in api._tos_plans() and "Starter" not in api._tos_plans()
    faq = api._faq()
    assert "What does Studio add?" in faq and "on Starter" not in faq


def test_a_studio_subscribers_screen_is_told_its_perks(world, monkeypatch):
    """Settings lists Studio's perks and shows Direct support from /me's
    plan_limits. They were once left out of /me, so a Studio subscriber's
    screen looked exactly like Pro's while every string test passed."""
    world.store.set_plan("pro1", "studio")
    lim = world.as_user("pro1").get("/me").json()["plan_limits"]
    assert lim["max_streams"] == 30
    assert lim["priority"] and lim["keep_files"] and lim["no_campaign_fee"] and lim["direct_support"]
    assert lim["keep_days"] == world.api.settings.clip_file_max_age_days
    lim = world.as_user("st1").get("/me").json()["plan_limits"]
    assert not (lim["priority"] or lim["keep_files"] or lim["no_campaign_fee"] or lim["direct_support"])


def test_kept_days_on_every_page_is_the_real_setting(monkeypatch):
    """"Kept N days" is a retention promise; it reads the same setting
    files.sweep() enforces, the way the Privacy Policy does."""
    from src.dashboard import api
    monkeypatch.setattr(api.settings, "clip_file_max_age_days", 45)
    assert "Clip videos kept 45 days" in api._pricing()
    assert "kept the full 45 days" in api._faq()
    assert "Clip videos kept 45 days" in api._paywall_plans()


def test_the_dashboard_knows_studio():
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert "studio: 'Studio'" in DASHBOARD_HTML
    assert "/billing/change?plan=studio" in DASHBOARD_HTML
    assert "me.plan_limits.direct_support" in DASHBOARD_HTML
    assert "Starter is $10/month" not in DASHBOARD_HTML
