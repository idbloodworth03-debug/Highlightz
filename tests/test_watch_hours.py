"""Free gets 5 hours of live watching a week, and can buy more at $1 an hour.

Owner, 2026-11-19: "lets get started with the free tiers only getting 5 hours
a week and they can pay for more and lets have it be 1$ an hour. Also make
sure we can track this in the admin page and see who buys it etc."

Held here: what is counted and in what order (the week's free hours, then
bought ones, which never expire); that only Free is metered; that an account
with nothing left is refused a slot, paused with its own hold, told once a
week, and NOT restarted by a visit; that it starts again the moment it has
hours; that a payment credits exactly once and only for the right amount;
the routes; the admin tab; the dashboard's meter and buy dialog; and that no
public page still promises Free "no time limit" while the meter is on.
"""

import asyncio
import inspect
import json
import re
import time

import pytest

from config.settings import settings
from src.billing import plans, watch_hours as wh
from src.dashboard import api
from tests.test_free_queue_pause import _events, scene  # noqa: F401  (the fixture)

H = 3600
FREE_S = 5 * H


_REAL_GRANDFATHERED = wh.grandfathered


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "free_watch_hours_week", 5)
    wh._reset_for_tests(tmp_path)
    # Every account below is a NEW one (made after the meter started), unless
    # a test says otherwise; grandfathering has its own tests at the end.
    monkeypatch.setattr(wh, "grandfathered", lambda user: False)
    monkeypatch.setattr(api, "_hours_held", set())
    monkeypatch.setattr(api, "_watch_mark", {})
    yield tmp_path
    wh._reset_for_tests()


FREE = {"id": "f", "username": "f", "subscription_status": "none"}


# ── 1. the meter ────────────────────────────────────────────────────────────

def test_free_hours_go_first_then_bought_ones():
    now = 2_000_000_000.0
    assert wh.left_s("f", FREE, now) == FREE_S
    assert wh.credit("f", 5, session_id="cs_1", amount_cents=500, now=now)
    assert wh.left_s("f", FREE, now) == FREE_S + 5 * H
    wh.consume("f", FREE, 4 * H, now)
    snap = wh.snapshot("f", FREE, now)
    assert snap["free_left_s"] == H and snap["paid_left_s"] == 5 * H, "bought hours were spent first"
    wh.consume("f", FREE, 2 * H, now)
    snap = wh.snapshot("f", FREE, now)
    assert snap["free_left_s"] == 0 and snap["paid_left_s"] == 4 * H


def test_the_week_turns_over_on_monday_and_bought_hours_carry_over():
    mon = wh.week_start(2_000_000_000.0)
    assert time.gmtime(mon).tm_wday == 0 and time.gmtime(mon).tm_hour == 0
    wh.credit("f", 10, session_id="cs_1", amount_cents=1000, now=mon + 10)
    wh.consume("f", FREE, FREE_S + 3 * H, mon + 100)
    assert wh.snapshot("f", FREE, mon + 200)["left_s"] == 7 * H
    nxt = wh.snapshot("f", FREE, mon + wh.WEEK_S + 1)
    assert nxt["free_left_s"] == FREE_S and nxt["paid_left_s"] == 7 * H
    assert nxt["resets_at"] == mon + 2 * wh.WEEK_S


def test_a_payment_credits_once_however_many_times_it_is_reported():
    assert wh.credit("f", 5, session_id="cs_same", amount_cents=500, via="webhook")
    assert not wh.credit("f", 5, session_id="cs_same", amount_cents=500, via="redirect")
    wh._reset_for_tests(wh._FILE.parent)                   # a restart reads it back from disk
    assert wh.snapshot("f", FREE)["paid_left_s"] == 5 * H
    assert [p["session_id"] for p in wh.purchases()] == ["cs_same"]


@pytest.mark.parametrize("user,metered", [
    (FREE, True),
    ({"id": "p", "subscription_status": "active", "plan": "pro"}, False),
    ({"id": "s", "subscription_status": "active", "plan": "studio"}, False),
    ({"id": "t", "subscription_status": "trialing", "trial_ends_at": time.time() + 86400}, False),
    ({"id": "a", "subscription_status": "none", "is_admin": True}, False),
    (None, False),
])
def test_only_free_is_metered(user, metered):
    assert wh.metered(user) is metered
    assert (wh.left_s("x", user) == float("inf")) is (not metered)


def test_zero_switches_the_whole_thing_off(monkeypatch):
    monkeypatch.setattr(settings, "free_watch_hours_week", 0)
    assert not wh.metered(FREE) and wh.snapshot("f", FREE) is None
    assert wh.public_words("short") == wh.public_words("long") == "no time limit"


def test_the_price_is_a_dollar_an_hour_in_packs():
    assert wh.PRICE_CENTS == 100 and wh.price_words() == "$1"
    assert wh.valid_pack(5) == 5 and wh.valid_pack("10") == 10
    for bad in (1, 0, -5, 7, 1000, "x", None):
        assert wh.valid_pack(bad) is None


def test_a_deleted_account_loses_its_balance_but_the_payment_record_stays():
    wh.credit("f", 5, session_id="cs_1", amount_cents=500)
    assert wh.forget_user("f") and "f" not in wh.all_rows()
    assert len(wh.purchases()) == 1
    assert "_wh_purge.forget_user(uid)" in inspect.getsource(api.delete_account)


# ── 2. counting a live slot ─────────────────────────────────────────────────

def test_a_held_slot_is_counted_and_never_more_than_two_minutes_at_once(scene):
    scene.person("f", pending=0)
    api._watch_mark["f:chan"] = 1000.0
    api._fold_watching("f:chan", 1030.0)
    assert wh.snapshot("f", scene.users["f"], 1030.0)["used_week_s"] == 30
    api._fold_watching("f:chan", 1030.0 + 9 * H)         # a suspended box is not nine hours
    assert wh.snapshot("f", scene.users["f"], 1030.0 + 9 * H)["used_week_s"] == 30 + 2 * api._WATCH_EVERY


def test_no_hours_left_means_no_slot_and_paid_plans_are_never_refused(scene):
    scene.person("f", pending=0)
    scene.person("p", pending=0, status="active", plan="pro")
    api._live_slots.clear()
    wh.consume("f", scene.users["f"], FREE_S)
    assert api.acquire_live_slot("f:chan") is False
    assert api.acquire_live_slot("p:chan") is True
    assert "p:chan" in api._watch_mark, "the clock did not start when the slot was taken"


def test_giving_a_slot_back_counts_up_to_that_moment(scene):
    scene.person("f", pending=0)
    api._live_slots.clear()
    assert api.acquire_live_slot("f:chan")
    api._watch_mark["f:chan"] = time.time() - 45
    api.release_live_slot("f:chan")
    assert "f:chan" not in api._watch_mark
    assert 44 <= wh.snapshot("f", scene.users["f"])["used_week_s"] <= 47


# ── 3. running out, and coming back ─────────────────────────────────────────

def _pass():
    return asyncio.run(api.watch_hours_once())


def test_running_out_pauses_with_its_own_hold_and_says_so_once_a_week(scene):
    scene.person("f", pending=0)
    scene.person("room", pending=0)
    wh.consume("f", scene.users["f"], FREE_S)
    assert _pass()["paused"] == 1
    assert scene.rec("f")["status"] == "paused" and scene.rec("f")["hold"] == "hours"
    assert scene.rec("room")["status"] == "live", "an account with hours left was paused"
    assert "f" in api._hours_held
    told = [m for e, u, m in scene.sent if e == "watch_hours" and u == "f"]
    assert told and told[0]["message"] and told[0]["hours"]["left_s"] == 0
    assert "inbox_changed" in _events(scene, "f")
    assert [n["title"] for _, n in scene.notes] == [api._HOURS_NOTICE_TITLE]
    # Running out again the same week does not ring the bell again.
    api._streams["f:chan"].update(status="live")
    api._streams["f:chan"].pop("hold", None)
    _pass()
    assert len(scene.notes) == 1


def test_a_visit_does_not_restart_an_account_that_is_out_of_hours(scene):
    scene.person("f", pending=0)
    wh.consume("f", scene.users["f"], FREE_S)
    _pass()
    assert asyncio.run(api._resume_user_streams("f")) == 0
    assert scene.rec("f")["status"] == "paused" and scene.rec("f")["hold"] == "hours"
    assert api._may_resume_now("f") is False


def test_hours_again_starts_it_within_the_minute(scene):
    scene.person("f", pending=0)
    wh.consume("f", scene.users["f"], FREE_S)
    _pass()
    scene.sent.clear()
    wh.credit("f", 5, session_id="cs_1", amount_cents=500)
    assert _pass()["resumed"] == 1
    assert scene.rec("f")["status"] == "starting" and "hold" not in scene.rec("f")
    assert "f" not in api._hours_held and ("chan", "f") in scene.started
    assert "stream_updated" in _events(scene, "f") and "watch_hours" in _events(scene, "f")


def test_upgrading_lifts_the_hold(scene):
    scene.person("f", pending=0)
    wh.consume("f", scene.users["f"], FREE_S)
    _pass()
    scene.users["f"].update(subscription_status="active", plan="pro")
    assert _pass()["resumed"] == 1 and scene.rec("f")["status"] == "starting"


# ── 4. paying ───────────────────────────────────────────────────────────────

def _session(uid="f", hours=5, amount=None, paid="paid", sid="cs_test_1", kind="watch_hours"):
    return {"id": sid, "payment_status": paid, "amount_total": hours * 100 if amount is None else amount,
            "metadata": {"kind": kind, "user_id": uid, "hours": str(hours)}}


def test_a_paid_session_credits_once_and_restarts_a_held_account(scene):
    scene.person("f", pending=0)
    wh.consume("f", scene.users["f"], FREE_S)
    _pass()
    scene.sent.clear()
    assert asyncio.run(api._hours_paid(_session(), "webhook")) is True
    assert asyncio.run(api._hours_paid(_session(), "redirect")) is False
    assert wh.snapshot("f", scene.users["f"])["paid_left_s"] == 5 * H
    assert scene.rec("f")["status"] == "starting"
    msg = [m for e, u, m in scene.sent if e == "watch_hours"][0]["message"]
    assert "5 watching hours added" in msg and "watched again" in msg


def test_buying_ahead_does_not_claim_a_restart(scene):
    scene.person("f", pending=0)
    assert asyncio.run(api._hours_paid(_session(), "webhook"))
    msg = [m for e, u, m in scene.sent if e == "watch_hours"][0]["message"]
    assert msg == "5 watching hours added."


@pytest.mark.parametrize("bad", [
    {"amount": 100}, {"amount": 0}, {"paid": "unpaid"}, {"hours": 7}, {"kind": "campaign"},
    {"uid": ""}, {"sid": ""},
])
def test_a_session_that_does_not_add_up_credits_nothing(scene, bad):
    scene.person("f", pending=0)
    assert asyncio.run(api._hours_paid(_session(**bad), "webhook")) is False
    assert wh.purchases() == []


def test_the_webhook_hands_hour_sessions_to_the_same_credit():
    src = open(api.__file__).read()
    hook = src[src.index('(session.get("metadata") or {}).get("kind") == "watch_hours"') - 400:][:900]
    assert "_hours_paid(" in hook and 'via="webhook"' in hook


def _req(uid, body=None, path="/billing/hours/checkout", method="POST", query=b""):
    from starlette.requests import Request
    raw = json.dumps(body or {}).encode()

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request({"type": "http", "method": method, "path": path, "headers": [],
                    "query_string": query, "session": {"auth": True, "user_id": uid}}, receive)


def test_checkout_is_free_only_a_real_pack_and_the_servers_price(scene, monkeypatch):
    from fastapi import HTTPException
    from src.billing import stripe_billing
    scene.person("f", pending=0)
    scene.person("p", pending=0, status="active", plan="pro")
    monkeypatch.setattr(api, "_current_user_id", lambda r: r.scope["session"]["user_id"])
    made = []
    monkeypatch.setattr(stripe_billing, "create_hours_checkout",
                        lambda user, hours: made.append((user["id"], hours)) or ("https://checkout.example/x", "cs_x"))
    monkeypatch.setattr(settings, "stripe_secret_key", "")
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.hours_checkout(_req("f", {"hours": 5})))
    assert e.value.status_code == 503
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_stub")
    for uid, body, code in (("p", {"hours": 5}, 400), ("f", {"hours": 3}, 400), ("f", {}, 400)):
        with pytest.raises(HTTPException) as e:
            asyncio.run(api.hours_checkout(_req(uid, body)))
        assert e.value.status_code == code
    assert asyncio.run(api.hours_checkout(_req("f", {"hours": 10}))) == {"url": "https://checkout.example/x"}
    assert made == [("f", 10)]


def test_the_stripe_session_charges_a_dollar_an_hour(monkeypatch):
    from src.billing import stripe_billing
    got = {}

    class _Sessions:
        @staticmethod
        def create(params):
            got.update(params)
            return type("S", (), {"url": "https://checkout.example/y", "id": "cs_y"})()
    fake = type("C", (), {})()
    fake.v1 = type("V1", (), {})()
    fake.v1.checkout = type("Co", (), {"sessions": _Sessions})()
    monkeypatch.setattr(stripe_billing, "_client", lambda: fake)
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_stub")
    url, sid = stripe_billing.create_hours_checkout({"id": "f", "email": ""}, 25)
    item = got["line_items"][0]
    assert got["mode"] == "payment" and item["quantity"] == 25
    assert item["price_data"]["unit_amount"] == 100 and item["price_data"]["currency"] == "usd"
    assert got["metadata"] == {"kind": "watch_hours", "user_id": "f", "hours": "25"}
    assert "{CHECKOUT_SESSION_ID}" in got["success_url"] and (url, sid) == ("https://checkout.example/y", "cs_y")


def test_coming_back_from_stripe_credits_only_your_own_session(scene, monkeypatch):
    from src.billing import stripe_billing
    scene.person("f", pending=0)
    scene.person("g", pending=0)
    monkeypatch.setattr(api, "_current_user_id", lambda r: r.scope["session"]["user_id"])
    monkeypatch.setattr(stripe_billing, "hours_checkout_session", lambda sid: _session(uid="f", sid=sid))
    r = asyncio.run(api.hours_paid_return(_req("g", method="GET", path="/billing/hours/paid"), session_id="cs_a"))
    assert wh.purchases() == [] and "hours=pending" in r.headers["location"]
    r = asyncio.run(api.hours_paid_return(_req("f", method="GET", path="/billing/hours/paid"), session_id="cs_a"))
    assert len(wh.purchases()) == 1 and "hours=bought" in r.headers["location"]


# ── 5. what people see ──────────────────────────────────────────────────────

def test_the_dashboard_has_the_meter_the_dialog_and_a_handler_for_every_event():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "msg.event==='watch_hours'" in page.replace(" ", "")
    for piece in ("function WatchHoursMeter(", "function BuyHoursDialog(", "/billing/hours/checkout",
                  "out of watching hours", "Buy hours to keep watching", "hz_buy_hours"):
        assert piece in page, piece
    src = open(api.__file__).read()
    assert '"watch_hours":         _watch_hours_for_me(uid, user)' in src, "/me does not carry the meter"


def test_every_event_the_meter_sends_is_one_the_dashboard_handles():
    src = open(api.__file__).read()
    sent = set(re.findall(r'"event":\s*"(watch_hours[a-z_]*)"', src))
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    for ev in sent:
        assert f"msg.event==='{ev}'" in page.replace(" ", ""), ev


def test_the_admin_page_shows_who_bought_and_who_is_out():
    from src.dashboard.api import ADMIN_HTML as page
    for piece in ('id="panel-hours"', "loadHours", "/admin/hours", "in watching hours this week",
                  "out of watching hours"):
        assert piece in page, piece


def test_the_admin_route_lists_purchases_newest_first_with_the_buyer(scene, monkeypatch):
    from src.auth import users as user_store
    scene.person("f", pending=0)
    scene.person("p", pending=0, status="active", plan="pro")
    monkeypatch.setattr(user_store, "get_all", lambda: list(scene.users.values()))
    monkeypatch.setattr(api, "_require_admin", lambda r: None)
    wh.credit("f", 5, session_id="cs_1", amount_cents=500, now=time.time() - 60)
    wh.credit("f", 10, session_id="cs_2", amount_cents=1000)
    wh.consume("f", scene.users["f"], 2 * H)
    wh.consume("p", scene.users["p"], 9 * H)          # not metered: counts nothing, listed nowhere
    out = asyncio.run(api.admin_hours(_req("admin", method="GET", path="/admin/hours")))
    assert [p["hours"] for p in out["purchases"]] == [10, 5]
    assert out["purchases"][0]["name"] == "f" and out["purchases"][0]["buyer_total"]["cents"] == 1500
    assert out["totals"]["all"] == {"purchases": 2, "buyers": 1, "hours": 15, "cents": 1500}
    assert [u["user_id"] for u in out["usage"]] == ["f"] and out["usage"][0]["used_s"] == 2 * H
    ov = api._hours_overview(time.time())
    assert ov["on"] and ov["week_buyers"] == 1


def test_no_public_page_promises_free_no_time_limit_while_the_meter_is_on():
    from src.dashboard import compare_html, compare_content, tutorial_content, tutorial_html
    import importlib
    for m in (compare_content, compare_html, tutorial_content, tutorial_html):
        importlib.reload(m)
    pages = {"landing": api.LANDING_HTML, "login": api.LOGIN_HTML, "terms": api.TOS_HTML,
             "compare": compare_html.render(), "tutorial_cta": tutorial_content.CTA_BODY,
             "tutorial_meta": tutorial_html._DESC}
    for name, html in pages.items():
        assert "no time limit" not in html.lower(), f"{name} still says no time limit"
    assert "5 live hours a week" in api.LANDING_HTML
    assert "Live watching" in api.LANDING_HTML and "hours of live monitoring" in api.TOS_HTML
    assert "Watching hours" in api.PRIVACY_HTML


def test_the_reaper_check_names_the_hours_pause():
    import importlib.util
    import pathlib
    path = pathlib.Path(api.__file__).resolve().parents[2] / "scripts" / "reaper_check.py"
    spec = importlib.util.spec_from_file_location("reaper_check_wh", path)
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)
    users = [{"id": "f", "username": "f", "subscription_status": "none"}]
    streams = [{"channel": "c", "user_id": "f", "status": "paused", "hold": "hours", "added_at": 1.0}]
    rows = R.classify(users, streams, [], {}, {}, time.time(), None)
    assert rows[0]["kind"] == "hours"
    text = "\n".join(R.report(rows, now=time.time(), started=None, changed=0.0, on_disk=True))
    assert "out of watching hours" in text
    assert "streams_paused_hours" in R.EVENTS and "watch_hours_error" in R.BROKEN


def test_reading_a_balance_never_writes_a_row_for_it():
    """The admin table reads every Free account every 30 seconds."""
    assert wh.snapshot("never", FREE)["left_s"] == FREE_S and wh.left_s("never", FREE) == FREE_S
    assert "never" not in wh.all_rows() and not wh.save_if_dirty()
    wh.consume("never", FREE, 60)
    assert "never" in wh.all_rows()


# ── grandfathered: accounts from before the meter keep unlimited watching ───
# Owner, 2026-11-20: "dont change any of the free acounts active rn grand
# father them in and the new ones are changed".

@pytest.fixture
def real(monkeypatch):
    monkeypatch.setattr(wh, "grandfathered", _REAL_GRANDFATHERED)


def test_the_start_is_written_once_and_never_moves(real, monkeypatch):
    first = wh.started_at()
    assert first and json.loads(wh._START.read_text())["at"] == first
    wh._start_at = None                                   # a restart reads it back
    monkeypatch.setattr(wh.time, "time", lambda: first + 86400)
    assert wh.started_at() == first


def test_accounts_from_before_are_never_metered_and_new_ones_are(real):
    start = wh.started_at()
    old = {"id": "o", "subscription_status": "none", "created_at": start - 3600}
    ancient = {"id": "a", "subscription_status": "none"}           # no created_at: an old record
    new = {"id": "n", "subscription_status": "none", "created_at": start + 60}
    assert not wh.metered(old) and not wh.metered(ancient) and wh.metered(new)
    assert wh.left_s("o", old) == float("inf") and wh.snapshot("o", old) is None
    assert wh.snapshot("n", new)["left_s"] == FREE_S


def test_meter_off_at_first_means_no_start_and_nobody_metered(real, monkeypatch):
    monkeypatch.setattr(settings, "free_watch_hours_week", 0)
    assert wh.started_at() is None and not wh._START.exists()


def test_a_start_that_cannot_be_written_meters_nobody(real, monkeypatch):
    def boom(*a, **k):
        raise OSError("read-only")
    monkeypatch.setattr(wh, "atomic_write_json", boom)
    assert wh.started_at() is None
    assert not wh.metered({"id": "n", "subscription_status": "none", "created_at": time.time() + 9})


def test_a_grandfathered_account_out_of_hours_before_is_started_again(real, scene):
    """Paused under the meter before it was grandfathered: the next pass
    finds it has no limit and starts it."""
    start = wh.started_at()
    scene.person("f", pending=0, created_at=start - 60)
    api._streams["f:chan"].update(status="paused", hold="hours")
    api._hours_held.add("f")
    assert asyncio.run(api.watch_hours_once())["resumed"] == 1
    assert scene.rec("f")["status"] == "starting"


def test_a_grandfathered_account_cannot_be_sold_hours(real, scene, monkeypatch):
    from fastapi import HTTPException
    start = wh.started_at()
    scene.person("old", pending=0, created_at=start - 60)
    monkeypatch.setattr(api, "_current_user_id", lambda r: r.scope["session"]["user_id"])
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_stub")
    with pytest.raises(HTTPException) as e:
        asyncio.run(api.hours_checkout(_req("old", {"hours": 5})))
    assert e.value.status_code == 400 and "without a limit" in e.value.detail


def test_the_terms_faq_and_admin_say_who_is_grandfathered(real):
    src = open(api.__file__).read()
    assert "neither do free accounts created before" in src and "keep unlimited watching" in src
    assert "Free accounts grandfathered (no limit)" in src and "grandfathered (account from before" in src
    assert api._hours_from_words()


def test_admin_counts_limited_and_grandfathered_free_accounts(real, scene, monkeypatch):
    from src.auth import users as user_store
    start = wh.started_at()
    scene.person("old", pending=0, created_at=start - 60)
    scene.person("new", pending=0, created_at=start + 60)
    scene.person("pro", pending=0, status="active", plan="pro", created_at=start + 60)
    monkeypatch.setattr(user_store, "get_all", lambda: list(scene.users.values()))
    monkeypatch.setattr(api, "_require_admin", lambda r: None)
    out = asyncio.run(api.admin_hours(_req("admin", method="GET", path="/admin/hours")))
    assert out["free_limited"] == 1 and out["free_grandfathered"] == 1 and out["started_at"] == start
