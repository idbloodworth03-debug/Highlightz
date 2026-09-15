"""Announcements: one message from the operator, in front of every user.

TWO DELIVERY PATHS, AND BOTH ARE TESTED, because a message only the people
online at the moment of sending saw would miss most of the accounts it was
written for. The send is a GLOBAL broadcast (the one genuinely global case),
and /announcements is pulled on mount and on every reconnect for everyone
else. Dismissal is persisted per account and broadcast to that account's
other tabs; retiring takes it down everywhere, including out of an open modal.

The modal itself sits ABOVE the editor and the clip modal, because the whole
job of this surface is to be impossible to miss.
"""

import base64
import json as _j
import time

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.dashboard import announcements as an
from src.dashboard import api

FRONTEND = __import__("pathlib").Path(__file__).resolve().parent.parent / "src/dashboard/aurora_html.py"
SRC = FRONTEND.read_text()

PEOPLE: dict = {}


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(an, "_FILE", tmp_path / "announcements.json")
    PEOPLE.clear()
    PEOPLE.update({
        "boss":  {"id": "boss", "username": "owner", "is_admin": True},
        "u1":    {"id": "u1", "username": "one", "plan": "free"},
        "u2":    {"id": "u2", "username": "two", "plan": "pro"},
    })
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "_load", lambda: list(PEOPLE.values()))
    monkeypatch.setattr(user_store, "_save", lambda users: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(user_store, "get_all", lambda: list(PEOPLE.values()))
    sent = []

    async def _record(event, user_id=None):
        sent.append((event, user_id))
    monkeypatch.setattr(api, "broadcast", _record)
    return sent


def _client(uid):
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    data = base64.b64encode(_j.dumps({
        "auth": True, "user_id": uid, "subscription_status": "active"}).encode())
    c.cookies.set("session", signer.sign(data).decode())
    return c


# ── the store ────────────────────────────────────────────────────────────────

def test_an_announcement_needs_a_title_and_a_message():
    with pytest.raises(ValueError, match="title"):
        an.create("", "hello")
    with pytest.raises(ValueError, match="message"):
        an.create("Hi", "   ")


def test_size_ceilings_are_enforced():
    with pytest.raises(ValueError, match="80"):
        an.create("x" * 81, "hello")
    with pytest.raises(ValueError, match="1000"):
        an.create("Hi", "x" * 1001)


def test_it_always_expires_and_days_are_clamped():
    a = an.create("Hi", "hello", days=500)
    assert a["expires_at"] - a["created_at"] == pytest.approx(an.MAX_DAYS * 86400, abs=2)
    b = an.create("Hi", "hello", days=0)
    assert b["expires_at"] - b["created_at"] == pytest.approx(86400, abs=2)
    c = an.create("Hi", "hello", days="garbage")
    assert c["expires_at"] - c["created_at"] == pytest.approx(an.DEFAULT_DAYS * 86400, abs=2)


def test_expired_rows_are_pruned_and_never_served():
    a = an.create("Old", "gone", days=1)
    assert an.active(now=time.time() + 2 * 86400) == [], "an expired row was served"
    # Age it past its expiry on disk, then write: the write prunes it.
    rows = an._load(); rows[0]["expires_at"] = time.time() - 1; an._save(rows)
    assert an.active() == []
    an.create("New", "here")
    assert [r["title"] for r in an._load()] == ["New"], "the write did not prune the expired row"


def test_a_user_sees_what_they_have_not_dismissed_oldest_first():
    a = an.create("First", "1"); time.sleep(0.01)
    b = an.create("Second", "2")
    assert [r["id"] for r in an.for_user({})] == [a["id"], b["id"]]
    assert [r["id"] for r in an.for_user({"announcements_seen": [a["id"]]})] == [b["id"]]


def test_retiring_removes_it_and_a_second_retire_is_false():
    a = an.create("Hi", "hello")
    assert an.retire(a["id"]) is True
    assert an.retire(a["id"]) is False
    assert an.active() == []


# ── sending, from the admin page ─────────────────────────────────────────────

def test_sending_is_admin_only(store):
    r = _client("u1").post("/admin/announcements", json={"title": "Hi", "body": "hello"})
    assert r.status_code in (403, 401, 302)
    assert an.active() == []
    assert store == []


def test_sending_broadcasts_globally_to_every_open_tab(store):
    r = _client("boss").post("/admin/announcements", json={"title": "Hi", "body": "hello", "days": 7})
    assert r.status_code == 201, r.text
    (event, uid), = store
    assert event["event"] == "announcement" and event["announcement"]["title"] == "Hi"
    assert uid is None, "the send must reach every user's sockets, not one user's"


def test_a_bad_send_is_a_400_with_the_reason(store):
    r = _client("boss").post("/admin/announcements", json={"title": "", "body": "hello"})
    assert r.status_code == 400 and "title" in r.json()["detail"]
    assert store == []


def test_the_admin_list_carries_a_read_receipt(store):
    boss = _client("boss")
    a = boss.post("/admin/announcements", json={"title": "Hi", "body": "hello"}).json()
    PEOPLE["u1"]["announcements_seen"] = [a["id"]]
    d = boss.get("/admin/announcements").json()
    assert d["users"] == 3
    assert d["rows"][0]["seen"] == 1


# ── receiving, on the dashboard ──────────────────────────────────────────────

def test_a_user_who_was_offline_gets_it_on_next_open(store):
    a = an.create("Hi", "hello")
    rows = _client("u1").get("/announcements").json()["rows"]
    assert [r["id"] for r in rows] == [a["id"]]
    assert "by" not in rows[0], "the sender's id has no business on a user's screen"


def test_dismissing_persists_and_closes_the_users_other_tabs(store):
    a = an.create("Hi", "hello")
    c = _client("u1")
    c.post(f"/announcements/{a['id']}/seen")
    assert PEOPLE["u1"]["announcements_seen"] == [a["id"]]
    assert c.get("/announcements").json()["rows"] == []
    (event, uid), = store
    assert event == {"event": "announcement_seen", "id": a["id"]} and uid == "u1"


def test_one_users_dismissal_is_not_anothers(store):
    a = an.create("Hi", "hello")
    _client("u1").post(f"/announcements/{a['id']}/seen")
    assert [r["id"] for r in _client("u2").get("/announcements").json()["rows"]] == [a["id"]]


def test_retiring_takes_it_down_everywhere(store):
    a = an.create("Hi", "hello")
    r = _client("boss").delete(f"/admin/announcements/{a['id']}")
    assert r.status_code == 200
    assert _client("u1").get("/announcements").json()["rows"] == []
    (event, uid), = store
    assert event == {"event": "announcement_retired", "id": a["id"]} and uid is None


def test_retiring_is_admin_only(store):
    a = an.create("Hi", "hello")
    _client("u1").delete(f"/admin/announcements/{a['id']}")
    assert an.get(a["id"]) is not None


# ── the realtime contract (CLAUDE.md) ────────────────────────────────────────

def test_every_emitted_event_has_a_handler():
    for ev in ("announcement", "announcement_retired", "announcement_seen"):
        assert f"msg.event==='{ev}'" in SRC, f"{ev} is emitted but never handled"


def test_it_is_pulled_on_mount_and_reconnect():
    ref = SRC[SRC.index("const refetchAll"):SRC.index("const wsBootstrapped")]
    assert "fetch('/announcements')" in ref


def test_the_modal_is_in_front_of_everything():
    css = SRC[SRC.index(".rd-ann-bg{"):SRC.index(".ed-bg{")]
    zi = int(__import__("re").search(r"z-index:(\d+)", css).group(1))
    editor = int(__import__("re").search(r"\.ed-bg\{[^}]*z-index:(\d+)", SRC).group(1))
    assert zi > editor, "an announcement would hide behind the editor"
    assert "position:fixed;inset:0" in css


def test_the_modal_is_rendered_with_a_way_to_dismiss():
    comp = SRC[SRC.index("function AnnouncementModal("):SRC.index("function ClipModal(")]
    assert 'role="dialog"' in comp and 'aria-modal="true"' in comp
    assert "onSeen(a.id)" in comp and "Got it" in comp
    assert "e.key === 'Escape'" in comp
    assert "<AnnouncementModal a={announcements[0]} onSeen={dismissAnnouncement}/>" in SRC
    assert "fetch('/announcements/'+encodeURIComponent(id)+'/seen',{method:'POST'})" in SRC


def test_the_admin_page_has_the_compose_form_and_the_list():
    html = api.ADMIN_HTML
    for need in ('data-tab="notify"', 'id="panel-notify"', 'id="an-title"', 'id="an-body"',
                 'id="an-send"', "fetch('/admin/announcements'", "'/admin/announcements/' + b.getAttribute('data-retire'), 'DELETE'"):
        assert need in html, f"admin page is missing {need}"
    assert "if(b.dataset.tab === 'notify' && !AN_LOADED) loadAnnouncements();" in html
