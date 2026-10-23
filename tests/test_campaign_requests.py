"""Campaign requests: streamers fund campaigns (owner, 2026-10-23).

"let the streamers be able to request a campaign for their streams after they
sign up with an account (which they can do for free) ... charge a streamer a
flat fee ... and we would take a small percent like 2-5% added on as a fee ...
they can also choose the amount of money they want to put into the campaign."

What these defend:
  1. the price: budget + flat fee + percentage, in whole cents, fixed when
     the request is sent and re-quoted only when it is edited;
  2. the form: every prize place adds up to the budget exactly, the budget is
     inside the configured range, a brief, a platform, terms agreed;
  3. the life of a request: who may move it where (the streamer edits and
     withdraws, an admin approves, asks for changes or declines with a note);
  4. the money: only an approved request can be paid, only the quote's exact
     total publishes it, the webhook and the redirect back cannot publish it
     twice, a second payment is flagged, and a session paid for someone else
     publishes nothing;
  5. what the payment makes: an ordinary published campaign, its prize pool
     the budget, its picture copied, linked back to the request;
  6. privacy and access: a request is its owner's and the admins' only, and
     the feature is admin-only until CAMPAIGN_REQUESTS_ENABLED;
  7. realtime: every change reaches the owner's tab as campaign_request_updated.
"""

import asyncio
import base64
import json
import time

import pytest
from itsdangerous import TimestampSigner

from src import campaigns as camp
from src.campaigns import requests as creq

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
NOW = time.time()


def ask(**kw):
    base = {"streamers": [{"name": "streamr", "platform": "twitch"}],
            "title": "Clip my week", "summary": "The best moments from this week's streams.",
            "brief": "Funny moments, clutch plays and chat losing it. Keep clips under a minute.",
            "rules": "No reuploads of somebody else's clip.",
            "budget": 500, "payouts": [300, 150, 50],
            "judging": "top_clip_views", "judging_n": 3,
            "start_mode": "asap", "duration_days": 14,
            "platforms": ["tiktok", "youtube"], "hashtags": "#streamr", "mentions": "@streamr",
            "caption_template": "", "contact_email": "", "agree": True}
    base.update(kw)
    return base


STREAMER = {"id": "streamr", "username": "streamr", "twitch_login": "streamr",
            "email": "streamr@example.com", "subscription_status": "none"}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(camp, "_INDEX", tmp_path / "campaigns.json")
    monkeypatch.setattr(camp, "_IMAGES", tmp_path / "campaign_images")
    monkeypatch.setattr(creq, "_INDEX", tmp_path / "campaign_requests.json")
    monkeypatch.setattr(creq, "_IMAGES", tmp_path / "campaign_request_images")
    monkeypatch.setattr(settings, "campaign_flat_fee_usd", 25.0)
    monkeypatch.setattr(settings, "campaign_fee_pct", 5.0)
    monkeypatch.setattr(settings, "campaign_min_budget_usd", 100.0)
    monkeypatch.setattr(settings, "campaign_max_budget_usd", 50000.0)
    for m in (camp, creq):
        (m._campaigns if m is camp else m._requests).clear()
        m._loaded = False
    yield
    for m in (camp, creq):
        (m._campaigns if m is camp else m._requests).clear()
        m._loaded = False


# ── 1. the price ─────────────────────────────────────────────────────────────

def test_the_quote_is_budget_plus_flat_fee_plus_percentage():
    q = creq.quote(500)
    assert (q["budget"], q["flat_fee"], q["percent_fee"], q["fees"], q["total"]) == \
        (500.0, 25.0, 25.0, 50.0, 550.0)
    assert q["total_cents"] == 55000


def test_the_percentage_is_rounded_to_the_cent_from_whole_cents(monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "campaign_fee_pct", 2.5)
    q = creq.quote(123.45)                  # 2.5% of 12345 cents = 308.625
    assert q["percent_fee_cents"] == 309 and q["total_cents"] == 12345 + 2500 + 309
    monkeypatch.setattr(settings, "campaign_fee_pct", 0)
    monkeypatch.setattr(settings, "campaign_flat_fee_usd", 0)
    assert creq.quote(100)["total_cents"] == 10000, "no fee means the budget alone"


def test_the_quote_is_fixed_when_sent_and_redone_on_edit(monkeypatch):
    from config.settings import settings
    r = creq.submit(STREAMER, ask())
    monkeypatch.setattr(settings, "campaign_fee_pct", 3.0)
    assert creq.get(r["id"])["quote"]["total"] == 550.0, "a fee change moved a price already shown"
    creq.request_changes(r["id"], "boss", "Shorter title please")
    r = creq.edit(r["id"], STREAMER, ask(title="Clip it"))
    assert r["quote"]["total"] == 540.0 and r["status"] == creq.SUBMITTED


# ── 2. the form ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad,msg", [
    ({"streamers": []}, "channel"),
    ({"streamers": [{"name": f"c{i}", "platform": "twitch"} for i in range(6)]}, "Up to 5"),
    ({"title": " "}, "name"),
    ({"summary": ""}, "one-line"),
    ({"brief": "clip me"}, "what to look for"),
    ({"budget": 99.99, "payouts": [99.99]}, "smallest budget"),
    ({"budget": 50000.01, "payouts": [50000.01]}, "largest budget"),
    ({"budget": "lots"}, "number"),
    ({"payouts": []}, "split"),
    ({"payouts": [300, 150]}, "add up to $450.00"),
    ({"payouts": [499.5, 0.5]}, "at least $1.00"),
    ({"budget": 2100, "payouts": [100] * 21}, "Up to 20"),
    ({"judging": "custom", "judging_note": ""}, "winners picked"),
    ({"start_mode": "date", "start_at": NOW - 60}, "future"),
    ({"start_mode": "date", "start_at": NOW + 200 * 86400}, "180 days"),
    ({"duration_days": 0}, "1 to 60"),
    ({"duration_days": 61}, "1 to 60"),
    ({"platforms": ["myspace"]}, "at least one place"),
    ({"hashtags": "#no-dashes"}, "valid hashtag"),
    ({"contact_email": "not an email"}, "email"),
    ({"agree": False}, "agree"),
])
def test_the_form_is_checked(bad, msg):
    with pytest.raises(creq.RequestError) as e:
        creq.submit(STREAMER, ask(**bad))
    assert msg in str(e.value)


def test_a_good_request_is_stored_with_its_quote():
    r = creq.submit(STREAMER, ask(hashtags="#streamr #clips"))
    assert r["status"] == creq.SUBMITTED and r["quote"]["total"] == 550.0
    assert r["payouts"] == [300.0, 150.0, 50.0] and r["hashtags"] == ["streamr", "clips"]
    assert r["contact_email"] == "streamr@example.com", "the account's email is the fallback"
    assert r["verified_owner"] is True, "signed in as the channel it asks about"
    assert creq.submit(STREAMER, ask(streamers=[{"name": "someone", "platform": "kick"}]))[
        "verified_owner"] is False
    creq._requests.clear(); creq._loaded = False            # survives a restart
    assert creq.get(r["id"])["title"] == "Clip my week"


def test_five_open_requests_at_most():
    for _ in range(creq.MAX_OPEN_PER_USER):
        last = creq.submit(STREAMER, ask())
    with pytest.raises(creq.RequestError, match="waiting already"):
        creq.submit(STREAMER, ask())
    creq.withdraw(last["id"], STREAMER["id"])
    creq.submit(STREAMER, ask())


def test_sending_and_withdrawing_in_a_loop_cannot_flood_the_owner():
    """Every request emails the owner; withdrawing frees an open slot, so the
    open limit alone would not stop a loop."""
    for _ in range(creq.MAX_SENT_PER_DAY):
        creq.withdraw(creq.submit(STREAMER, ask())["id"], STREAMER["id"])
    with pytest.raises(creq.RequestError, match="a lot of requests for one day"):
        creq.submit(STREAMER, ask())
    for r in creq._requests.values():             # a day later
        r["created_at"] -= 86400 + 60
    creq.submit(STREAMER, ask())


# ── 3. the life of a request ─────────────────────────────────────────────────

def test_who_may_move_a_request_where():
    r = creq.submit(STREAMER, ask())
    with pytest.raises(creq.RequestError, match="Say why"):
        creq.request_changes(r["id"], "boss", "")
    with pytest.raises(creq.RequestError, match="Say why"):
        creq.decline(r["id"], "boss", " ")
    creq.approve(r["id"], "boss")
    with pytest.raises(creq.RequestError, match="no longer be changed"):
        creq.edit(r["id"], STREAMER, ask())
    with pytest.raises(creq.RequestError):
        creq.approve(r["id"], "boss")
    creq.request_changes(r["id"], "boss", "Lower the first prize")
    assert creq.get(r["id"])["admin_note"] == "Lower the first prize"
    creq.edit(r["id"], STREAMER, ask(payouts=[250, 200, 50]))
    creq.decline(r["id"], "boss", "Not a fit")
    for act in (lambda: creq.approve(r["id"], "boss"),
                lambda: creq.withdraw(r["id"], STREAMER["id"]),
                lambda: creq.edit(r["id"], STREAMER, ask())):
        with pytest.raises(creq.RequestError):
            act()
    assert [h["status"] for h in creq.get(r["id"])["history"]] == [
        "submitted", "approved", "changes_requested", "submitted", "declined"]


def test_somebody_elses_request_is_not_found():
    r = creq.submit(STREAMER, ask())
    with pytest.raises(KeyError):
        creq.withdraw(r["id"], "other")
    with pytest.raises(KeyError):
        creq.edit(r["id"], {"id": "other"}, ask())


# ── 4/5. the money, and what it makes ────────────────────────────────────────

def test_only_an_approved_request_paid_in_full_publishes():
    r = creq.submit(STREAMER, ask())
    with pytest.raises(creq.RequestError, match="Paid while submitted"):
        creq.publish(r["id"], payment="stripe", amount_cents=55000)
    creq.approve(r["id"], "boss")
    with pytest.raises(creq.RequestError, match="54999 cents"):
        creq.publish(r["id"], payment="stripe", amount_cents=54999)
    assert creq.get(r["id"])["status"] == creq.APPROVED and not camp.listing(include_drafts=True)
    r, c = creq.publish(r["id"], payment="stripe", amount_cents=55000, session_id="cs_1")
    assert r["status"] == creq.PUBLISHED and r["amount_paid_cents"] == 55000
    assert r["campaign_id"] == c["id"] and r["checkout_session_id"] == "cs_1"
    again, c2 = creq.publish(r["id"], payment="stripe", amount_cents=55000, session_id="cs_1")
    assert c2["id"] == c["id"] and len(camp.listing(include_drafts=True)) == 1, "published twice"


def test_the_campaign_it_makes():
    r = creq.submit(STREAMER, ask())
    creq.set_image(r["id"], STREAMER["id"], PNG)
    creq.approve(r["id"], "boss")
    before = time.time()
    r, c = creq.publish(r["id"], payment="stripe", amount_cents=55000)
    assert c["published"] and c["request_id"] == r["id"]
    assert c["prize_pool"] == 500.0 and c["payouts"] == [300.0, 150.0, 50.0], \
        "the budget is the prize pool, in full"
    assert before <= c["start_at"] <= time.time() and c["end_at"] - c["start_at"] == 14 * 86400
    assert c["streamers"][0]["url"] == "https://www.twitch.tv/streamr"
    assert c["rules"].startswith("What to clip\nFunny moments") and "Rules\nNo reuploads" in c["rules"]
    assert c["hashtags"] == ["streamr"] and c["mentions"] == ["streamr"]
    assert camp.image_path(c["id"]).read_bytes() == PNG, "the picture came across"
    assert camp.status_of(c) == "live"


def test_a_future_start_is_kept_and_a_passed_one_starts_now():
    later = NOW + 3 * 86400
    r = creq.submit(STREAMER, ask(start_mode="date", start_at=later))
    assert creq.campaign_form(r, now=NOW)["start_at"] == later
    form = creq.campaign_form(r, now=later + 60)
    assert form["start_at"] == later + 60 and form["end_at"] == later + 60 + 14 * 86400


def test_a_comp_publishes_without_payment():
    r = creq.submit(STREAMER, ask())
    r, c = creq.publish(r["id"], payment="comped", by="boss")
    assert r["payment"] == "comped" and r["amount_paid_cents"] == 0 and c["published"]
    declined = creq.submit(STREAMER, ask())
    creq.decline(declined["id"], "boss", "no")
    with pytest.raises(creq.RequestError):
        creq.publish(declined["id"], payment="comped")


def test_the_owner_does_not_see_admin_fields():
    r = creq.submit(STREAMER, ask())
    creq.set_image(r["id"], STREAMER["id"], PNG)
    creq.set_checkout(r["id"], "cs_secret")
    mine = creq.public(r)
    for k in ("image", "history", "reviewed_by", "checkout_session_id"):
        assert k not in mine
    assert mine["image_url"].startswith(f"/campaign-requests/{r['id']}/image")
    admin = creq.public(r, admin=True)
    assert admin["checkout_session_id"] == "cs_secret" and admin["history"]


def test_the_picture_is_sniffed_and_only_changes_while_editable():
    r = creq.submit(STREAMER, ask())
    with pytest.raises(creq.RequestError, match="PNG, JPEG or WebP"):
        creq.set_image(r["id"], STREAMER["id"], b"<svg onload=alert(1)>")
    with pytest.raises(KeyError):
        creq.set_image(r["id"], "other", PNG)
    creq.approve(r["id"], "boss")
    with pytest.raises(creq.RequestError):
        creq.set_image(r["id"], STREAMER["id"], PNG)


# ── the API ──────────────────────────────────────────────────────────────────

PEOPLE = {
    "boss": {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
    "streamr": dict(STREAMER),
    "other": {"id": "other", "username": "other", "email": "other@example.com",
              "subscription_status": "none"},
}


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.billing import stripe_billing as sb
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(user_store, "_load", lambda: [dict(u) for u in PEOPLE.values()])
    monkeypatch.setattr(user_store, "_save", lambda u: None)
    monkeypatch.setattr(api.settings, "campaign_requests_enabled", True)
    monkeypatch.setattr(api.settings, "stripe_secret_key", "sk_test_x")
    sent, emails, expired = [], [], []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id, msg))
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api, "_tell_owner_email", lambda s, t: emails.append((s, t)))
    monkeypatch.setattr(api, "_expire_checkout_later",
                        lambda r: expired.append(r.get("checkout_session_id")))
    # Stripe, as far as these endpoints can tell.
    stripe = {"sessions": {}, "expired": expired}

    def create(req, user):
        sid = f"cs_test_{len(stripe['sessions']) + 1}"
        stripe["sessions"][sid] = {"id": sid, "payment_status": "unpaid",
                                   "amount_total": req["quote"]["total_cents"],
                                   "metadata": {"kind": "campaign_request",
                                                "request_id": req["id"], "user_id": req["user_id"]}}
        return "https://checkout.stripe.com/c/" + sid, sid
    monkeypatch.setattr(sb, "create_campaign_checkout", create)
    monkeypatch.setattr(sb, "campaign_checkout_session", lambda sid: stripe["sessions"].get(sid))
    monkeypatch.setattr(sb, "expire_checkout", lambda sid: expired.append(sid))

    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        if uid:
            p = PEOPLE[uid]
            c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
                {"auth": True, "user_id": uid, "username": uid, "is_admin": p.get("is_admin", False),
                 "subscription_status": p["subscription_status"]}).encode())).decode())
        return c

    def session():
        return json.loads(base64.b64decode(signer.unsign(c.cookies.get("session"))))

    def paid(sid):
        stripe["sessions"][sid]["payment_status"] = "paid"
    c.login, c.session, c.paid, c.api = login, session, paid, api
    c.sent, c.emails, c.stripe = sent, emails, stripe
    return c


def _events(client, event, user_id):
    return [m for e, u, m in client.sent if e == event and u == user_id]


def test_it_is_admin_only_until_switched_on(client, monkeypatch):
    monkeypatch.setattr(client.api.settings, "campaign_requests_enabled", False)
    assert client.login("streamr").get("/campaign-requests").status_code == 404
    assert client.login("streamr").post("/campaign-requests", json=ask()).status_code == 404
    assert client.login("boss").get("/campaign-requests").status_code == 200
    assert client.login(None).get("/campaign-requests",
                                  headers={"accept": "application/json"}).status_code == 401


def test_a_free_account_asks_and_hears_back_live(client):
    me = client.login("streamr").get("/campaign-requests").json()
    assert me["rows"] == [] and me["pricing"] == {"flat_fee": 25.0, "fee_pct": 5.0,
                                                  "min_budget": 100.0, "max_budget": 50000.0}
    assert me["account"]["channels"] == [{"platform": "twitch", "name": "streamr"}]
    r = client.post("/campaign-requests", json=ask())
    assert r.status_code == 200, r.text
    rid = r.json()["id"]
    assert r.json()["quote"]["total"] == 550.0 and "history" not in r.json()
    assert _events(client, "campaign_request_updated", "streamr")[-1]["status"] == "submitted"
    assert client.emails and "Clip my week" in client.emails[0][0], "the owner was not told"
    assert [x["id"] for x in client.get("/campaign-requests").json()["rows"]] == [rid]
    bad = client.post("/campaign-requests", json=ask(payouts=[1]))
    assert bad.status_code == 400 and "add up to" in bad.json()["detail"]


def test_requests_are_private_to_their_owner(client):
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    client.login("streamr").post(f"/campaign-requests/{rid}/image",
                                 files={"file": ("x.png", PNG, "image/png")})
    other = client.login("other")
    assert other.get("/campaign-requests").json()["rows"] == []
    assert other.put(f"/campaign-requests/{rid}", json=ask()).status_code == 404
    assert other.post(f"/campaign-requests/{rid}/withdraw").status_code == 404
    assert other.post(f"/campaign-requests/{rid}/checkout").status_code == 404
    assert other.get(f"/campaign-requests/{rid}/image").status_code == 404
    assert other.post(f"/admin/campaign-requests/{rid}/approve", json={}).status_code == 403
    assert client.login("boss").get(f"/campaign-requests/{rid}/image").content == PNG
    assert client.login("streamr").get(f"/campaign-requests/{rid}/image").content == PNG


def test_the_admin_reviews_and_the_streamer_hears_each_step(client):
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    boss = client.login("boss")
    rows = boss.get("/admin/campaign-requests").json()["rows"]
    assert rows[0]["user"]["email"] == "streamr@example.com" and rows[0]["history"]
    r = boss.post(f"/admin/campaign-requests/{rid}/changes", json={"note": ""})
    assert r.status_code == 400 and "Say why" in r.json()["detail"]
    assert boss.post(f"/admin/campaign-requests/{rid}/changes",
                     json={"note": "Add a hashtag"}).json()["status"] == "changes_requested"
    told = _events(client, "campaign_request_updated", "streamr")[-1]
    assert told["status"] == "changes_requested" and "changes" in told["message"]
    client.login("streamr").put(f"/campaign-requests/{rid}", json=ask(hashtags="#a #b"))
    assert client.login("boss").post(f"/admin/campaign-requests/{rid}/approve",
                                     json={}).json()["status"] == "approved"
    assert "$550.00" in _events(client, "campaign_request_updated", "streamr")[-1]["message"]


def test_pay_then_come_back_publishes_it(client):
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    assert client.post(f"/campaign-requests/{rid}/checkout").status_code == 400, \
        "paid before it was approved"
    client.login("boss").post(f"/admin/campaign-requests/{rid}/approve", json={})
    out = client.login("streamr").post(f"/campaign-requests/{rid}/checkout").json()
    assert out["url"].startswith("https://checkout.stripe.com/")
    sid = creq.get(rid)["checkout_session_id"]
    back = client.get(f"/campaign-requests/{rid}/paid?session_id={sid}", follow_redirects=False)
    assert back.headers["location"] == "/?view=promote&campaign_pay=pending", "unpaid published"
    client.paid(sid)
    back = client.get(f"/campaign-requests/{rid}/paid?session_id={sid}", follow_redirects=False)
    assert back.headers["location"] == f"/?view=promote&campaign_paid={rid}"
    r = creq.get(rid)
    assert r["status"] == "published" and r["payment"] == "stripe"
    assert camp.get(r["campaign_id"])["published"]
    assert ("campaigns_changed", None) in [(e, u) for e, u, _ in client.sent]
    assert _events(client, "campaign_request_updated", "streamr")[-1]["status"] == "published"
    assert any("Campaign paid" in s for s, _ in client.emails)


def test_the_webhook_publishes_it_once(client, monkeypatch):
    from src.billing import stripe_billing as sb
    monkeypatch.setattr(client.api.settings, "stripe_webhook_secret", "whsec_x")
    monkeypatch.setattr(sb, "handle_webhook_event", lambda payload, sig: json.loads(payload))
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    client.login("boss").post(f"/admin/campaign-requests/{rid}/approve", json={})
    client.login("streamr").post(f"/campaign-requests/{rid}/checkout")
    sid = creq.get(rid)["checkout_session_id"]
    client.paid(sid)

    def deliver(event_id, session):
        return client.login(None).post("/billing/webhook", content=json.dumps(
            {"id": event_id, "type": "checkout.session.completed",
             "data": {"object": session}}), headers={"stripe-signature": "t=1,v1=x"})
    assert deliver("evt_1", client.stripe["sessions"][sid]).status_code == 200
    r = creq.get(rid)
    assert r["status"] == "published"
    deliver("evt_2", client.stripe["sessions"][sid])       # Stripe retries; the redirect lands too
    client.login("streamr").get(f"/campaign-requests/{rid}/paid?session_id={sid}")
    assert len(camp.listing(include_drafts=True)) == 1, "published twice"


def test_a_payment_that_does_not_match_publishes_nothing(client):
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    client.login("boss").post(f"/admin/campaign-requests/{rid}/approve", json={})
    client.login("streamr").post(f"/campaign-requests/{rid}/checkout")
    sid = creq.get(rid)["checkout_session_id"]
    client.paid(sid)
    # somebody else's session id pasted into the return link
    client.stripe["sessions"][sid]["metadata"]["user_id"] = "other"
    client.login("streamr").get(f"/campaign-requests/{rid}/paid?session_id={sid}")
    assert creq.get(rid)["status"] == "approved"
    # the right account, the wrong amount
    client.stripe["sessions"][sid]["metadata"]["user_id"] = "streamr"
    client.stripe["sessions"][sid]["amount_total"] = 100
    client.login("streamr").get(f"/campaign-requests/{rid}/paid?session_id={sid}")
    assert creq.get(rid)["status"] == "approved" and not camp.listing(include_drafts=True)


def test_a_new_checkout_closes_the_last_and_a_paid_one_is_not_charged_again(client):
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    client.login("boss").post(f"/admin/campaign-requests/{rid}/approve", json={})
    s = client.login("streamr")
    s.post(f"/campaign-requests/{rid}/checkout")
    first = creq.get(rid)["checkout_session_id"]
    s.post(f"/campaign-requests/{rid}/checkout")
    assert first in client.stripe["expired"], "two open checkouts could both be paid"
    second = creq.get(rid)["checkout_session_id"]
    assert second != first
    client.paid(second)                     # paid; the webhook has not come yet
    out = s.post(f"/campaign-requests/{rid}/checkout").json()
    assert out == {"url": "", "published": True}, "a paid request was sent to pay again"
    assert len(client.stripe["sessions"]) == 2 and creq.get(rid)["status"] == "published"


def test_a_second_payment_is_flagged(client, monkeypatch):
    errors = []
    monkeypatch.setattr(client.api.log, "error", lambda ev, **kw: errors.append(ev))
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    client.login("boss").post(f"/admin/campaign-requests/{rid}/approve", json={})
    client.login("streamr").post(f"/campaign-requests/{rid}/checkout")
    sid = creq.get(rid)["checkout_session_id"]
    client.paid(sid)
    client.get(f"/campaign-requests/{rid}/paid?session_id={sid}")
    extra = dict(client.stripe["sessions"][sid], id="cs_other")
    asyncio.run(client.api._campaign_paid(extra, via="webhook"))
    assert "campaign_paid_twice" in errors


def test_withdrawing_or_a_review_closes_an_open_checkout(client):
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    client.login("boss").post(f"/admin/campaign-requests/{rid}/approve", json={})
    client.login("streamr").post(f"/campaign-requests/{rid}/checkout")
    sid = creq.get(rid)["checkout_session_id"]
    client.login("boss").post(f"/admin/campaign-requests/{rid}/changes", json={"note": "Fix dates"})
    assert sid in client.stripe["expired"]
    assert client.login("streamr").post(f"/campaign-requests/{rid}/checkout").status_code == 400
    client.stripe["expired"].clear()
    client.login("streamr").post(f"/campaign-requests/{rid}/withdraw")
    assert client.stripe["expired"] == [sid] and creq.get(rid)["status"] == "cancelled"


def test_no_stripe_no_checkout(client, monkeypatch):
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    client.login("boss").post(f"/admin/campaign-requests/{rid}/approve", json={})
    monkeypatch.setattr(client.api.settings, "stripe_secret_key", "")
    assert client.login("streamr").post(f"/campaign-requests/{rid}/checkout").status_code == 503


def test_an_admin_can_comp_or_check_a_payment(client):
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    assert client.login("streamr").post(f"/admin/campaign-requests/{rid}/comp").status_code == 403
    r = client.login("boss").post(f"/admin/campaign-requests/{rid}/comp").json()
    assert r["status"] == "published" and r["payment"] == "comped"
    assert _events(client, "campaign_request_updated", "streamr")[-1]["status"] == "published"
    # a payment whose webhook never came and whose payer never came back
    rid = client.login("streamr").post("/campaign-requests", json=ask()).json()["id"]
    client.login("boss").post(f"/admin/campaign-requests/{rid}/approve", json={})
    client.login("streamr").post(f"/campaign-requests/{rid}/checkout")
    client.paid(creq.get(rid)["checkout_session_id"])
    r = client.login("boss").post(f"/admin/campaign-requests/{rid}/check-payment").json()
    assert r["status"] == "published" and r["payment"] == "stripe"


def test_expiring_later_closes_the_session(monkeypatch):
    from src.dashboard import api
    from src.billing import stripe_billing as sb
    calls = []
    monkeypatch.setattr(sb, "expire_checkout", calls.append)

    async def go():
        api._expire_checkout_later({"checkout_session_id": "cs_9"})
        api._expire_checkout_later({"checkout_session_id": ""})
        await asyncio.gather(*list(api._notify_tasks))
    asyncio.run(go())
    assert calls == ["cs_9"]


# ── signing up from the Campaigns page lands on the form ─────────────────────

def test_sign_in_from_the_campaigns_page_returns_to_the_form(client, monkeypatch):
    signed_in = client.login("streamr").get("/login?next=promote", follow_redirects=False)
    assert signed_in.headers["location"] == "/?view=promote&new=1"
    client.login(None).get("/login?next=promote")
    assert client.session()["after_login"] == "promote"

    async def _account(password):
        return PEOPLE["streamr"]
    monkeypatch.setattr(client.api, "_password_account", _account)
    r = client.post("/login", data={"password": "x"}, follow_redirects=False)
    assert r.headers["location"] == "/?view=promote&new=1"
    assert "after_login" not in client.session(), "it outlived the sign-in"


def test_next_is_a_key_not_a_url(client):
    client.login(None).get("/login?next=https://evil.example")
    assert "after_login" not in (client.session() if client.cookies.get("session") else {})
    assert client.api._after_sign_in("https://evil.example") == "/"
    assert client.api._after_sign_in(None) == "/"


def test_me_says_whether_the_screen_is_on(client, monkeypatch):
    feats = client.login("streamr").get("/me").json()["features"]
    assert feats["campaign_requests"] is True
    monkeypatch.setattr(client.api.settings, "campaign_requests_enabled", False)
    assert client.login("streamr").get("/me").json()["features"]["campaign_requests"] is False


# ── the public page and the Campaigns tab ───────────────────────────────────

def test_the_page_is_hidden_until_switched_on_except_from_admins(client, monkeypatch):
    monkeypatch.setattr(client.api.settings, "campaign_requests_enabled", False)
    assert client.login(None).get("/promote").status_code == 404
    assert client.login("streamr").get("/promote").status_code == 404
    assert client.login("boss").get("/promote").status_code == 200, "an admin cannot preview it"
    monkeypatch.setattr(client.api.settings, "campaign_requests_enabled", True)
    assert client.login(None).get("/promote", follow_redirects=False).status_code == 200


def test_request_a_campaign_leads_through_sign_up_to_the_form(client):
    page = client.login(None).get("/promote").text
    assert 'href="/login?next=promote" class="btn btn-go btn-lg">Request a campaign' in page
    page = client.login("streamr").get("/promote").text
    assert 'href="/?view=promote&amp;new=1" class="btn btn-go btn-lg">Request a campaign' in page


def test_every_price_on_the_page_is_the_real_price(client, monkeypatch):
    from src.dashboard import promote_html
    monkeypatch.setattr(client.api.settings, "campaign_flat_fee_usd", 10.0)
    monkeypatch.setattr(client.api.settings, "campaign_fee_pct", 3.0)
    page = client.login(None).get("/promote").text
    assert "<b>$10 per campaign plus 3% of the budget</b>" in page
    q = creq.quote(1000)
    assert 'data-flat="1000" data-bp="300" data-min="10000" data-max="5000000"' in page
    assert '<b id="pc-total">$' + "{:,.2f}".format(q["total"]) + "</b>" in page
    for b in promote_html._EXAMPLE_BUDGETS:
        assert "<td>$" + "{:,.2f}".format(creq.quote(b)["total"]) + "</td>" in page
    # The calculator's arithmetic is quote()'s, in whole cents, half up.
    assert "Math.floor((b * bp + 5000) / 10000)" in promote_html._JS
    for cents in (1, 49, 50, 12345, 123456, 5000000):
        assert (cents * 300 + 5000) // 10000 == creq.quote(cents / 100)["percent_fee_cents"]


def test_the_campaigns_tab_is_on_every_public_bar_once_switched_on(client, monkeypatch):
    pages = ["/", "/tutorial", "/compare", "/blog", "/tos", "/privacy", "/cookies"]
    for p in pages:
        html = client.login(None).get(p).text
        assert '<a href="/promote" class="nav-link">Campaigns</a>' in html, f"{p}: no tab"
        assert '<a href="/blog">Blog</a><a href="/promote">Campaigns</a>' in html, f"{p}: no footer link"
    page = client.login(None).get("/promote").text
    assert '<a href="/promote" class="nav-link on" aria-current="page">Campaigns</a>' in page
    assert "/promote" in client.get("/sitemap.xml").text
    monkeypatch.setattr(client.api.settings, "campaign_requests_enabled", False)
    for p in pages:
        html = client.login(None).get(p).text
        assert 'href="/promote"' not in html, f"{p} advertises a page that answers 404"
    assert "/promote" not in client.get("/sitemap.xml").text


def test_the_terms_and_the_privacy_policy_cover_campaigns(client):
    tos = client.login(None).get("/tos").text
    assert '<h2 id="campaigns">4A. Campaigns</h2>' in tos
    for phrase in ("The budget is the prize pool.", "the prizes not won are refunded to you",
                   "we refund the price in full", "Permission to clip."):
        assert phrase in tos, phrase
    privacy = client.get("/privacy").text
    assert "<strong>Campaign requests</strong>" in privacy
    assert "A campaign request that was never paid is deleted with your account." in privacy
    assert 'href="/tos#campaigns"' in client.get("/promote").text


def test_deleting_an_account_takes_its_unpaid_requests_and_keeps_paid_ones(client, monkeypatch):
    from src.billing import stripe_billing as sb
    open_r = creq.submit(STREAMER, ask(title="Open one"))
    creq.set_image(open_r["id"], STREAMER["id"], PNG)
    approved = creq.submit(STREAMER, ask(title="Approved one"))
    creq.approve(approved["id"], "boss")
    creq.set_checkout(approved["id"], "cs_open")
    paid = creq.submit(STREAMER, ask(title="Paid one"))
    creq.approve(paid["id"], "boss")
    creq.publish(paid["id"], payment="stripe", amount_cents=55000)
    image = creq.image_path(open_r["id"])
    closed = []
    monkeypatch.setattr(sb, "expire_checkout", closed.append)
    # Everything else the deletion touches, kept off the real files.
    from src.auth import users as user_store
    from src.feedback import reviews
    from src.publish import connections, schedule
    from src.stats import downloads, stream_stats
    from src.uploads import library
    for mod in (library, schedule, connections, reviews, stream_stats, downloads):
        monkeypatch.setattr(mod, "delete_all_for_user", lambda uid: 0)
    monkeypatch.setattr(user_store, "delete", lambda uid: None)
    monkeypatch.setattr(client.api, "_clips", {})
    monkeypatch.setattr(client.api, "_streams", {})
    monkeypatch.setattr(client.api, "_save_clips", lambda: None)
    monkeypatch.setattr(client.api, "_save_streams", lambda: None)
    assert client.login("streamr").delete("/account").status_code == 200
    assert [r["title"] for r in creq.for_user("streamr")] == ["Paid one"]
    assert closed == ["cs_open"] and not image.exists()


# ── the dashboard and the admin page ────────────────────────────────────────

def test_the_promote_screen_is_wired_live():
    """CLAUDE.md's contract: the event has a handler, the list is in
    refetchAll, and the screen follows access from /me."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "{id:'promote',label:'Promote',icon:'megaphone',promoteOnly:true}" in page
    assert "(!n.promoteOnly||canPromoteFor(me))" in page
    assert "(me.features || {}).campaign_requests" in page
    ra = page[page.index("const refetchAll"):page.index("const wsBootstrapped")]
    assert "if(canPromoteFor(data)) refetchPromo();" in ra
    ws = page[page.index("msg.event==='campaign_request_updated'"):][:400]
    assert "refetchPromo()" in ws and "flash(msg.message)" in ws
    cc = page[page.index("msg.event==='campaigns_changed'"):][:400]
    assert "refetchPromo()" in cc, "a published request's campaign dates would go stale"
    # The way back from /promote's sign-up and from Stripe's checkout.
    assert "_params.get('view') === 'promote'" in page and "setPromoNew(true)" in page
    # A streamer here to fund a campaign never meets "Add a channel to watch".
    gate = page[page.index("const promoting = "):page.index("return <FirstRun")]
    assert "route === 'promote'" in gate and "!promoting" in gate
    for fn in ("function PromoteScreen(", "function PromoteForm(", "function PromoteCard(", "function pmQuote("):
        assert fn in page, fn


def test_the_form_prices_like_the_server():
    """pmQuote is quote() in JS: whole cents, the percentage rounded half up."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    fn = page[page.index("function pmQuote("):][:400]
    assert "Math.floor((b * bp + 5000) / 10000)" in fn


def test_the_admin_page_reviews_requests():
    from src.dashboard import api
    html = api.ADMIN_HTML
    for piece in ('id="cq-wrap"', 'id="cq-filters"', 'id="cq-switch"', "api('/admin/campaign-requests')",
                  "'/admin/campaign-requests/' + id + '/' + act", 'data-act="approve"', 'data-act="changes"',
                  'data-act="decline"', 'data-act="comp"', 'data-act="check-payment"', "if(!cqTyping()) loadRequests();"):
        assert piece in html, piece
