"""A failing card loses the paid plan, and loses nothing else.

Owner, 2026-11-16: "for the people whose cards are failing we need to do
something about that so they don't have access to the site". Until then a
past_due subscriber kept their tier for the whole of Stripe's retry window;
that day two accounts were weeks past due and running ten-channel plans.

The decision: no payment, no paid plan. The account is on Free until Stripe
collects. What these tests hold is the other half, which is what makes that
safe to do to a customer whose card merely expired:

  * the channels Free does not cover are HELD (paused, marked), not deleted;
  * nothing but payment brings them back: not opening the site, not changing
    a preset, not the idle loop;
  * the moment the charge goes through they restart by themselves;
  * if Stripe gives up instead, they are removed, as for any cancellation.

plans.get_plan, the webhook's wording and the sweep are in test_billing_grace
and test_subscription_reconcile; this file is the channels.
"""

import asyncio
import pathlib
import time

import pytest

from src.dashboard import api

NOW = time.time()


class _Stop(Exception):
    pass


@pytest.fixture
def scene(tmp_path, monkeypatch):
    users, started, stopped, sent, notes = {}, [], [], [], []

    async def _new(channel, platform, preset, uid=""):
        started.append(channel)

    async def _rm(channel, uid=""):
        stopped.append(channel)

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id, msg))

    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: users.get(uid))
    monkeypatch.setattr(user_store, "add_notices", lambda n: notes.append(dict(n)) or len(n))
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_paused_users", set())
    monkeypatch.setattr(api, "_user_last_active", {})
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_ACTIVITY_FILE", tmp_path / "activity.json")
    monkeypatch.setattr(api, "_publish_new_stream", _new)
    monkeypatch.setattr(api, "_publish_remove_stream", _rm)
    monkeypatch.setattr(api, "broadcast", _bcast)
    api._live_slots.clear()

    def person(uid, status, plan="pro", channels=4, **extra):
        users[uid] = {"id": uid, "username": uid, "subscription_status": status,
                      **({"plan": plan} if plan else {}), "stripe_customer_id": "cus_" + uid, **extra}
        api._user_last_active[uid] = NOW - 60
        for i in range(channels):
            key = f"{uid}:ch{i}"
            api._streams[key] = {"channel": f"ch{i}", "platform": "kick", "preset": "default",
                                 "status": "live", "user_id": uid, "added_at": 100.0 + i}
            api._live_slots.add(key)

    def set_status(uid, status, **extra):
        users[uid] = dict(users[uid], subscription_status=status, **extra)

    def chans(uid):
        return {r["channel"]: (r["status"], r.get("hold")) for k, r in sorted(api._streams.items())
                if k.startswith(uid + ":")}

    def tick():
        calls = {"n": 0}

        async def fake_sleep(_s):
            calls["n"] += 1
            if calls["n"] > 1:
                raise _Stop()
        monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)
        with pytest.raises(_Stop):
            asyncio.run(api.idle_stream_reaper())

    return type("Scene", (), {"person": staticmethod(person), "set_status": staticmethod(set_status),
                              "chans": staticmethod(chans), "tick": staticmethod(tick), "users": users,
                              "started": started, "stopped": stopped, "sent": sent, "notes": notes})


HELD = ("paused", "billing")


# ── holding ──────────────────────────────────────────────────────────────────

def test_a_failing_card_keeps_one_channel_running_and_holds_the_rest(scene):
    scene.person("u1", "past_due", channels=4)
    n = asyncio.run(api._enforce_stream_limit("u1"))
    assert n == 3
    assert scene.chans("u1") == {"ch0": ("live", None), "ch1": HELD, "ch2": HELD, "ch3": HELD}, \
        "the channel added first is the one that keeps running; nothing is deleted"
    assert sorted(scene.stopped) == ["ch1", "ch2", "ch3"]
    assert api._live_slots == {"u1:ch0"}, "a held channel still holds a live slot"
    assert [e for e, _u, _m in scene.sent] == ["stream_updated"] * 3
    assert all(m["stream"].get("hold") == "billing" for _e, _u, m in scene.sent), \
        "the open tab is not told WHY they went quiet"


def test_holding_twice_changes_nothing_and_stays_quiet(scene):
    scene.person("u1", "past_due", channels=4)
    asyncio.run(api._enforce_stream_limit("u1"))
    scene.sent.clear(); scene.stopped.clear()
    assert asyncio.run(api._enforce_stream_limit("u1")) == 0
    assert scene.sent == [] and scene.stopped == []


def test_a_real_ending_still_removes_what_free_does_not_cover(scene):
    """The partner test. A cancellation is not a hold."""
    scene.person("u1", "inactive", channels=4)
    assert asyncio.run(api._enforce_stream_limit("u1")) == 3
    assert scene.chans("u1") == {"ch0": ("live", None)}


def test_a_held_channel_does_not_count_against_the_server(scene):
    scene.person("u1", "past_due", channels=4)
    asyncio.run(api._enforce_stream_limit("u1"))
    assert api._counted_stream_keys() == ["u1:ch0"]


# ── nothing but payment brings them back ─────────────────────────────────────

def test_opening_the_site_does_not_restart_held_channels(scene):
    """Coming back restarts every channel the idle loop paused. If that
    included held ones, a failing card would get its ten channels back by
    refreshing the page."""
    scene.person("u1", "past_due", channels=3)
    asyncio.run(api._enforce_stream_limit("u1"))
    asyncio.run(api._pause_user_streams("u1"))            # ...and then they went idle
    scene.started.clear()
    assert asyncio.run(api._resume_user_streams("u1")) == 1
    assert scene.started == ["ch0"]
    assert scene.chans("u1") == {"ch0": ("starting", None), "ch1": HELD, "ch2": HELD}


def test_changing_a_preset_does_not_start_a_held_channel():
    """patch_stream restarts the worker so new rules apply. It did that for a
    paused channel too, which was a way round the hold."""
    import inspect
    src = inspect.getsource(api.patch_stream)
    at = src.index("if rec.get(\"status\") != \"paused\":")
    assert at < src.index("await _publish_new_stream(channel, platform, req.preset, uid)")


def test_the_five_minute_loop_leaves_a_failing_card_held(scene):
    scene.person("u1", "past_due", channels=3)
    scene.tick()
    assert scene.chans("u1")["ch1"] == HELD and scene.chans("u1")["ch2"] == HELD
    scene.started.clear()
    scene.tick()
    assert scene.started == [] and scene.chans("u1")["ch1"] == HELD


def test_the_clean_up_tool_does_not_clear_a_failing_cards_channels():
    from src.dashboard import cleanup
    assert cleanup.is_free({"id": "a", "subscription_status": "past_due", "plan": "pro"}) is False
    assert cleanup.is_free({"id": "a", "subscription_status": "inactive", "plan": "pro"}) is True


# ── the way back ─────────────────────────────────────────────────────────────

def test_the_charge_going_through_restarts_every_held_channel(scene):
    scene.person("u1", "past_due", channels=4)
    asyncio.run(api._enforce_stream_limit("u1"))
    scene.set_status("u1", "active")
    scene.sent.clear()
    assert asyncio.run(api._settle_billing_hold("u1")) == 3
    assert scene.started == ["ch1", "ch2", "ch3"], "oldest first"
    assert scene.chans("u1") == {"ch0": ("live", None), "ch1": ("starting", None),
                                 "ch2": ("starting", None), "ch3": ("starting", None)}
    assert [e for e, _u, _m in scene.sent] == ["stream_updated"] * 3
    assert all("paused_at" not in r for r in api._streams.values())


def test_the_loop_restarts_them_even_if_no_path_remembered_to(scene):
    """The safety net: whatever recorded `active` (webhook, sweep, an admin
    grant, a path written next year), the channels are back within 5 minutes."""
    scene.person("u1", "past_due", channels=3)
    scene.tick()
    scene.set_status("u1", "active")
    scene.started.clear()
    scene.tick()
    assert scene.started == ["ch1", "ch2"]
    assert all(st != "paused" for st, _h in scene.chans("u1").values())


def test_coming_back_on_a_smaller_plan_restarts_what_it_covers(scene):
    """Held on Pro, paid again as Starter (3 channels): the plan is enforced
    first, the newest go, the rest restart."""
    scene.person("u1", "past_due", plan="pro", channels=5)
    asyncio.run(api._enforce_stream_limit("u1"))
    scene.set_status("u1", "active", plan="starter")
    assert asyncio.run(api._settle_billing_hold("u1")) == 2
    assert scene.chans("u1") == {"ch0": ("live", None), "ch1": ("starting", None), "ch2": ("starting", None)}


def test_stripe_giving_up_removes_the_held_channels(scene):
    scene.person("u1", "past_due", channels=4)
    asyncio.run(api._enforce_stream_limit("u1"))
    scene.set_status("u1", "inactive")
    assert asyncio.run(api._settle_billing_hold("u1")) == 0
    assert scene.chans("u1") == {"ch0": ("live", None)}, "a cancellation kept channels Free does not cover"
    assert scene.started == []


def test_room_on_free_is_filled_from_the_held_channels(scene):
    """Still failing, and they removed the one channel that was running: the
    oldest held one takes its place instead of the account watching nothing."""
    scene.person("u1", "past_due", channels=3)
    asyncio.run(api._enforce_stream_limit("u1"))
    del api._streams["u1:ch0"]
    assert asyncio.run(api._settle_billing_hold("u1")) == 1
    assert scene.chans("u1") == {"ch1": ("starting", None), "ch2": HELD}
    assert asyncio.run(api._settle_billing_hold("u1")) == 0, "it kept releasing past the allowance"


def test_an_account_with_nothing_held_is_not_touched(scene):
    scene.person("u1", "active", channels=3)
    assert asyncio.run(api._settle_billing_hold("u1")) == 0
    assert scene.sent == [] and scene.started == [] and scene.stopped == []


# ── being told ───────────────────────────────────────────────────────────────

def test_an_account_already_past_due_is_caught_by_the_loop_and_told_once(scene):
    """kipcrap and unkn0wnballer on the day this shipped: past due for weeks,
    no webhook coming. The loop holds their channels and tells them, once."""
    scene.person("u1", "past_due", channels=3)
    scene.tick()
    events = [e for e, _u, _m in scene.sent]
    assert events.count("subscription_expired") == 1 and events.count("inbox_changed") == 1
    msg = next(m for e, _u, m in scene.sent if e == "subscription_expired")["message"]
    assert "did not go through" in msg and "ended" not in msg
    assert len(scene.notes) == 1 and scene.notes[0]["u1"]["title"] == "Your payment did not go through"
    scene.sent.clear()
    scene.tick()
    assert [e for e, _u, _m in scene.sent if e in ("subscription_expired", "inbox_changed")] == []
    assert len(scene.notes) == 1, "told again five minutes later"


def test_a_paying_account_is_never_held(scene):
    """What must not happen on the normal path."""
    scene.person("u1", "active", channels=4)
    scene.person("u2", "trialing", channels=4, trial_ends_at=NOW + 5 * 86400)
    scene.tick()
    for uid in ("u1", "u2"):
        assert all(h is None for _st, h in scene.chans(uid).values()), uid
    assert scene.stopped == [] and scene.notes == []


# ── what they see ────────────────────────────────────────────────────────────

SRC = pathlib.Path("src/dashboard/aurora_html.py").read_text()


def test_the_dashboard_says_so_on_every_screen_and_sends_them_to_the_portal():
    at = SRC.index("{me.subscription_status==='past_due' && <div")
    bar = SRC[at:SRC.index("<main className={'rd-screen'", at)]
    assert "Your payment did not go through." in bar and "Free plan" in bar
    assert 'href="/billing/portal"' in bar and "Update card" in bar
    assert "/billing/checkout" not in bar, "a failing card must fix its subscription, not buy a second"


def test_the_account_screen_offers_update_card_not_upgrade():
    assert "past_due:'Payment failed — on Free'" in SRC
    at = SRC.index("{sub==='past_due' && <div className=\"rd-field\">")
    row = SRC[at:at + 700]
    assert 'href="/billing/portal"' in row and "Update card" in row
    assert "{!isSubscribed && sub!=='trialing' && sub!=='past_due' && <div className=\"rd-field\">" in SRC, \
        "the upgrade row is shown to somebody who already has a subscription"


def test_a_held_channel_says_why_it_is_paused():
    assert SRC.count("==='billing' ? 'paused · payment failed' : 'paused · resumes when you return'") == 2


def test_no_new_event_went_out_without_a_handler():
    """Everything this sends is an event the dashboard already handles."""
    for ev in ("subscription_expired", "subscription_active", "billing_changed", "inbox_changed", "stream_updated"):
        assert f"msg.event==='{ev}'" in SRC, ev


# ── the whole way through the real webhook ───────────────────────────────────
# Everything above calls the pieces. This drives Stripe's own events through
# _process_stripe_event with nothing of ours stubbed but Stripe and the
# workers: the charge fails, the channels are held; the charge goes through,
# they are back.

@pytest.fixture
def wired(monkeypatch, tmp_path):
    import json
    from src.auth import users as user_store
    from src.billing import stripe_billing
    from config.settings import settings

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_stub")
    monkeypatch.setattr(settings, "stripe_price_id_pro", "price_pro")
    (tmp_path / "users.json").write_text(json.dumps([
        {"id": "u1", "username": "nova", "stripe_customer_id": "cus_1",
         "subscription_status": "active", "plan": "pro", "created_at": time.time()}]))

    started, stopped, sent = [], [], []

    async def _new(channel, platform, preset, uid=""):
        started.append(channel)

    async def _rm(channel, uid=""):
        stopped.append(channel)

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))

    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_paused_users", set())
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_publish_new_stream", _new)
    monkeypatch.setattr(api, "_publish_remove_stream", _rm)
    monkeypatch.setattr(api, "broadcast", _bcast)
    api._live_slots.clear()
    for i in range(4):
        api._streams[f"u1:ch{i}"] = {"channel": f"ch{i}", "platform": "twitch", "preset": "default",
                                     "status": "live", "user_id": "u1", "added_at": 100.0 + i}

    state = {"subs": []}

    class _R:
        def __init__(self, d): self.data = d

    class _C:
        def __init__(self): self.subscriptions = self
        def list(self, params=None): return _R(state["subs"])
    monkeypatch.setattr(stripe_billing, "_client", lambda: _C())

    def fire(status, sub_id="sub_A"):
        state["subs"] = [{"id": sub_id, "status": status}]
        api._stripe_processed.clear()
        ev = {"id": f"evt_{sub_id}_{status}_{len(sent)}", "type": "customer.subscription.updated",
              "data": {"object": {"id": sub_id, "customer": "cus_1", "status": status,
                                  "metadata": {"user_id": "u1"},
                                  "items": {"data": [{"price": {"id": "price_pro"}}]}}}}
        return asyncio.run(api._process_stripe_event(ev, time.time(), ev["id"]))

    def chans():
        return {r["channel"]: (r["status"], r.get("hold")) for _k, r in sorted(api._streams.items())}

    from src.billing.plans import get_plan
    return type("W", (), {"fire": staticmethod(fire), "chans": staticmethod(chans), "sent": sent,
                          "started": started, "stopped": stopped,
                          "user": staticmethod(lambda: user_store.get_by_id("u1")),
                          "plan": staticmethod(lambda: get_plan(user_store.get_by_id("u1")))})


def test_the_charge_fails_then_goes_through(wired):
    wired.fire("past_due")
    assert wired.plan() == "free" and wired.user()["plan"] == "pro"
    assert wired.chans() == {"ch0": ("live", None), "ch1": HELD, "ch2": HELD, "ch3": HELD}
    assert sorted(wired.stopped) == ["ch1", "ch2", "ch3"]
    events = [e for e, _u in wired.sent]
    assert events.count("subscription_expired") == 1 and "inbox_changed" in events
    assert wired.user()["notices"][-1]["title"] == "Your payment did not go through"

    wired.sent.clear()
    wired.fire("active")
    assert wired.plan() == "pro"
    assert wired.started == ["ch1", "ch2", "ch3"]
    assert all(h is None and st != "paused" for st, h in wired.chans().values())
    events = [e for e, _u in wired.sent]
    assert events.count("subscription_active") == 1
    assert events.index("stream_updated") < events.index("subscription_active"), \
        "the tab was told the plan is back before the channels were"


def test_the_charge_fails_and_stripe_gives_up(wired):
    wired.fire("past_due")
    wired.sent.clear()
    wired.fire("canceled")
    assert wired.user()["subscription_status"] == "inactive" and wired.plan() == "free"
    assert wired.chans() == {"ch0": ("live", None)}, "held channels outlived the subscription"
    assert wired.started == []
    assert ("subscription_expired", "u1") in wired.sent
