"""The notifications bell (owner, 2026-10-14).

"Can you make a little notification tab for users that is in like the top of
the screen somewhere that displays all announcements, discount code offers,
updates and things of that sort so users can see those things again if they
want"

The popups are unchanged; the bell is where what they said can be found again.
What these defend:
  1. GET /inbox lists every announcement still showing — dismissed or not —
     newest first, labelled announcement or update; retired and expired ones
     leave it;
  2. the account's discount offer stays in it after "Not now", with its code,
     and "Claim it" from the bell still opens checkout WITH the code applied —
     while the popup itself stays dismissed; paying, trialing and staff
     accounts never see one;
  3. unread = not acknowledged anywhere (the popup's Got it / Not now / Claim,
     or opening the bell after it arrived); opening it clears the badge, is
     persisted, and is broadcast to that user's other tabs only;
  4. it needs a session;
  5. the realtime wiring (CLAUDE.md): the new event has a handler, the bell is
     in refetchAll, and every event that changes its contents re-reads it;
  6. the bell sits in the header beside the account chip, and the admin can
     send an Update as well as an Announcement.
"""

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.auth import users as user_store
from src.dashboard import announcements as an
from src.dashboard import api

SRC = (__import__("pathlib").Path(__file__).resolve().parent.parent
       / "src/dashboard/aurora_html.py").read_text()

PEOPLE: dict = {}


@pytest.fixture(autouse=True)
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(an, "_FILE", tmp_path / "announcements.json")
    PEOPLE.clear()
    PEOPLE.update({
        "boss":   {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
        "fan":    {"id": "fan", "username": "fan", "subscription_status": "none"},
        "other":  {"id": "other", "username": "other", "subscription_status": "none"},
        "paying": {"id": "paying", "username": "paying", "subscription_status": "active"},
        "trial":  {"id": "trial", "username": "trial", "subscription_status": "trialing"},
    })
    monkeypatch.setattr(user_store, "_load", lambda: list(PEOPLE.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(user_store, "get_all", lambda: list(PEOPLE.values()))
    sent = []

    async def _record(msg, user_id=None):
        sent.append((msg.get("event"), user_id, msg))
    monkeypatch.setattr(api, "broadcast", _record)
    return sent


def _as(uid):
    c = TestClient(api.app, base_url="https://testserver")
    p = PEOPLE[uid]
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
        {"auth": True, "user_id": uid, "username": uid, "is_admin": p.get("is_admin", False),
         "subscription_status": p.get("subscription_status", "none")}).encode())).decode())
    return c


def _inbox(uid):
    r = _as(uid).get("/inbox")
    assert r.status_code == 200, r.text
    return r.json()


def _send(title="New: Autopilot", body="It posts for you.", kind=None, days=14):
    payload = {"title": title, "body": body, "days": days}
    if kind is not None:
        payload["kind"] = kind
    r = _as("boss").post("/admin/announcements", json=payload)
    assert r.status_code == 201, r.text
    return r.json()


def _offer(uid="fan", code="CLIPPER"):
    r = _as("boss").post(f"/admin/users/{uid}/offer",
                         json={"code": code, "plan": "pro", "headline": "50% off your first month"})
    assert r.status_code == 200, r.text


# ── 1. announcements and updates ─────────────────────────────────────────────

def test_every_live_announcement_is_listed_dismissed_or_not_newest_first():
    a = _send("First")
    _send("Second", kind="update")
    PEOPLE["fan"]["announcements_seen"] = [a["id"]]          # pressed Got it on the first
    rows = {r["id"]: r for r in an._load()}
    rows[a["id"]]["created_at"] -= 60                         # sent a minute apart
    an._save(list(rows.values()))
    items = _inbox("fan")["items"]
    assert [i["title"] for i in items] == ["Second", "First"]
    assert [i["kind"] for i in items] == ["update", "announcement"]
    assert items[1]["body"] == "It posts for you."
    # The popup list still leaves out what was dismissed — the bell does not.
    assert [r["title"] for r in _as("fan").get("/announcements").json()["rows"]] == ["Second"]


def test_retired_and_expired_announcements_leave_the_bell():
    a = _send("Going")
    _send("Staying")
    assert _as("boss").delete(f"/admin/announcements/{a['id']}").status_code == 200
    rows = an._load()
    rows.append({"id": "old", "title": "Old", "body": "x", "created_at": 1.0, "expires_at": 2.0})
    an._save(rows)
    assert [i["title"] for i in _inbox("fan")["items"]] == ["Staying"]


def test_an_update_is_labelled_and_an_unknown_kind_is_an_announcement(world):
    _send("Shiny", kind="update")
    sent = [m for e, uid, m in world if e == "announcement"]
    assert sent[-1]["announcement"]["kind"] == "update", "the live popup would say Announcement"
    assert _send("Odd", kind="<script>")["kind"] == "announcement"
    # Rows sent before `kind` existed read as announcements.
    rows = an._load()
    rows.append({"id": "legacy", "title": "Legacy", "body": "x",
                 "created_at": time.time(), "expires_at": time.time() + 999})
    an._save(rows)
    legacy = [i for i in _inbox("fan")["items"] if i["title"] == "Legacy"]
    assert legacy[0]["kind"] == "announcement"


# ── 2. the offer ─────────────────────────────────────────────────────────────

def test_the_offer_is_in_the_bell_with_its_code():
    _offer()
    (item,) = [i for i in _inbox("fan")["items"] if i["kind"] == "offer"]
    assert item["code"] == "CLIPPER" and item["plan"] == "pro"
    assert item["headline"] == "50% off your first month" and item["read"] is False


def test_not_now_closes_the_popup_but_the_bell_keeps_the_offer():
    _offer()
    assert _as("fan").post("/offer/dismiss").status_code == 200
    assert _as("fan").get("/me").json()["offer"] is None, "the popup came back"
    (item,) = [i for i in _inbox("fan")["items"] if i["kind"] == "offer"]
    assert item["code"] == "CLIPPER"
    assert item["read"] is True, "a dismissed offer still lights the badge"


def test_claiming_from_the_bell_after_not_now_applies_the_code(monkeypatch):
    from src.billing import stripe_billing
    monkeypatch.setattr(api.settings, "stripe_secret_key", "sk_test")
    monkeypatch.setattr(api.settings, "stripe_price_id_pro", "price_pro")
    calls = []

    async def fake_create(uid, username, price_id, customer_id=None, trial_days=0, promo_code=None):
        calls.append(promo_code)
        return "https://checkout.stripe.test/s"
    monkeypatch.setattr(stripe_billing, "create_checkout_url", fake_create)
    _offer()
    _as("fan").post("/offer/dismiss")
    r = _as("fan").get("/billing/checkout?plan=pro&offer=1", follow_redirects=False)
    assert r.status_code in (302, 307)
    assert calls == ["CLIPPER"], "Claim it from the bell opened checkout without the code"
    # Still only the account's OWN offer: someone without one gets no code.
    _as("other").get("/billing/checkout?plan=pro&offer=1", follow_redirects=False)
    assert calls[-1] is None


@pytest.mark.parametrize("uid", ["paying", "trial", "boss"])
def test_no_offer_in_the_bell_for_accounts_that_cannot_use_one(uid):
    _offer(uid)
    assert not [i for i in _inbox(uid)["items"] if i["kind"] == "offer"]


def test_an_offer_taken_back_leaves_the_bell():
    _offer()
    assert _as("boss").delete("/admin/users/fan/offer").status_code == 200
    assert not [i for i in _inbox("fan")["items"] if i["kind"] == "offer"]


# ── 3. unread ────────────────────────────────────────────────────────────────

def test_unread_counts_what_was_not_acknowledged_anywhere():
    a = _send("One")
    _send("Two")
    _offer()
    assert _inbox("fan")["unread"] == 3
    _as("fan").post(f"/announcements/{a['id']}/seen")       # Got it on its popup
    assert _inbox("fan")["unread"] == 2


def test_opening_the_bell_reads_everything_persists_and_tells_only_that_user(world):
    _send("One")
    _offer()
    world.clear()
    assert _as("fan").post("/inbox/read").status_code == 200
    assert PEOPLE["fan"]["inbox_read_at"] > 0
    assert [(e, uid) for e, uid, _ in world] == [("inbox_changed", "fan")]
    box = _inbox("fan")
    assert box["unread"] == 0 and all(i["read"] for i in box["items"])
    assert _inbox("other")["unread"] == 1, "one user's bell read another's"


def test_something_sent_after_opening_is_unread_again():
    _send("Before")
    _as("fan").post("/inbox/read")
    rows = an._load()
    for r in rows:
        r["created_at"] -= 60                                # sent a minute ago,
    an._save(rows)
    PEOPLE["fan"]["inbox_read_at"] -= 30                     # the bell opened since
    _send("After")
    box = _inbox("fan")
    assert box["unread"] == 1
    assert [i["title"] for i in box["items"] if not i["read"]] == ["After"]


def test_a_resent_offer_is_new_again():
    _offer()
    PEOPLE["fan"]["offer"]["created_at"] -= 100              # sent a while ago
    _as("fan").post("/offer/dismiss")
    first = [i for i in _inbox("fan")["items"] if i["kind"] == "offer"][0]
    assert first["read"] is True
    _offer()
    again = [i for i in _inbox("fan")["items"] if i["kind"] == "offer"][0]
    assert again["read"] is False and again["id"] != first["id"]


# ── 4. it needs a session ────────────────────────────────────────────────────

def test_a_stranger_gets_nothing():
    anon = TestClient(api.app, base_url="https://testserver")
    assert anon.get("/inbox", follow_redirects=False).status_code != 200
    assert anon.post("/inbox/read", follow_redirects=False).status_code != 200
    assert "/inbox" not in api._OPEN_PATHS


# ── 5. realtime (CLAUDE.md) ──────────────────────────────────────────────────

def test_the_new_event_has_a_handler_that_rereads_the_bell():
    i = SRC.index("msg.event==='inbox_changed'")
    assert "refetchInbox()" in SRC[i:i + 200]


def test_the_bell_is_pulled_on_mount_and_every_reconnect():
    ref = SRC[SRC.index("const refetchAll"):SRC.index("const wsBootstrapped")]
    assert "refetchInbox();" in ref
    fn = SRC[SRC.index("const refetchInbox"):SRC.index("const refetchInbox") + 300]
    assert "fetch('/inbox')" in fn


@pytest.mark.parametrize("event", ["msg.event==='announcement' && msg.announcement",
                                   "msg.event==='announcement_retired'",
                                   "msg.event==='offer_changed'"])
def test_every_event_that_changes_the_bell_rereads_it(event):
    i = SRC.index(event)
    branch = SRC[i:SRC.index("else if(", i + len(event))]
    assert "refetchInbox();" in branch, f"{event} does not re-read the bell"


def test_opening_marks_read_here_at_once_and_on_the_server():
    fn = SRC[SRC.index("const markInboxRead"):SRC.index("const markInboxRead") + 300]
    assert "unread:0" in fn and "fetch('/inbox/read',{method:'POST'})" in fn
    bell = SRC[SRC.index("function InboxBell("):SRC.index("// AUTO-EDIT ON A CLIP")]
    assert "onRead()" in bell and "e.key === 'Escape'" in bell


# ── 6. where it is, and the admin side ───────────────────────────────────────

def test_the_bell_is_in_the_header_beside_the_account_chip():
    start = SRC.index('<header className="rd-header">')
    head = SRC[start:SRC.index("</header>", start)]
    assert head.index("<InboxBell inbox={inbox} onRead={markInboxRead}/>") \
        < head.index('className="rd-user-chip"')
    bell = SRC[SRC.index("function InboxBell("):SRC.index("// AUTO-EDIT ON A CLIP")]
    assert 'aria-expanded={open}' in bell and 'role="dialog"' in bell
    item = SRC[SRC.index("function InboxItem("):SRC.index("function InboxBell(")]
    assert "'/billing/checkout?plan=' + (item.plan || 'pro') + '&offer=1'" in item
    assert "Claim it" in item


def test_the_phone_header_stays_one_row():
    """Measured in Chromium: with the bell, the header wrapped at 360-390px and
    left the avatar alone on a second row (58px -> 106px). On a phone it must
    not wrap; the title gives way instead — and that rule has to come AFTER
    the older phone block that turns wrapping on, or it loses."""
    wrap_on = SRC.index(".rd-header{flex-wrap:wrap;")
    fix = SRC.index(".rd-header{flex-wrap:nowrap;gap:4px}")
    assert fix > wrap_on, "the no-wrap rule is beaten by the older wrap rule"
    block = SRC[fix:SRC.index("}\n</style>", fix)]
    assert ".rd-header .htitle{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}" in block
    assert ".rd-header .rd-inbox" in block and "flex-shrink:0" in block


def test_the_panel_is_opaque():
    """A .97 fill let the screen below read through at a fifth of its
    brightness on a phone (checked in Chromium)."""
    rule = SRC[SRC.index(".rd-inbox-panel{position:absolute"):]
    rule = rule[:rule.index("}")]
    assert "background:#100e16" in rule and "rgba(" not in rule.split("background:")[1].split(";")[0]


def test_the_popup_says_update_for_an_update():
    modal = SRC[SRC.index("function AnnouncementModal("):SRC.index("function OfferModal(")]
    assert "a.kind==='update'?'Update':'Announcement'" in modal


def test_the_admin_can_send_an_update():
    html = api.ADMIN_HTML
    assert 'id="an-kind"' in html and '<option value="update">' in html
    assert "JSON.stringify({title, body, days, kind})" in html
    assert "r.kind === 'update' ? 'Update' : 'Announcement'" in html
