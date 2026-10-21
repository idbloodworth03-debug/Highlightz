"""Crash reports from the dashboard (POST /client-error).

WHY IT EXISTS. luclips reported a black screen on his iPhone and the dashboard
was loaded 35 times in the log window, and all the journal could say was that
pages were loaded: a screen that broke in someone's browser left nothing on
our side. The page now sends
what broke (hzReport and the ErrorBoundary in aurora_html.py; their own tests
are in test_iphone_black_screen.py) and this logs it as `client_error`.

What these pin is the other half of the deal: it is untrusted input from a
browser, so it is signed-in only, read with a cap before it is parsed, cut
short field by field, and limited per account, so a page stuck in a crash
loop cannot flood the journal or the alert emails.
"""

import base64
import json

import pytest
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.dashboard import api


class _Log:
    def __init__(self):
        self.lines = []

    def __getattr__(self, level):
        def _log(event, **kw):
            self.lines.append((level, event, kw))
        return _log

    def reports(self):
        return [(lv, kw) for lv, e, kw in self.lines if e == "client_error"]


@pytest.fixture
def rec(monkeypatch):
    r = _Log()
    monkeypatch.setattr(api, "log", r)
    monkeypatch.setattr(api, "_client_error_hits", {})
    return r


@pytest.fixture
def client(monkeypatch):
    from src.auth import users as user_store
    people = {"u1": {"id": "u1", "subscription_status": "active", "plan": "pro"},
              "u2": {"id": "u2", "subscription_status": "active", "plan": "pro"}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    c = TestClient(api.app)

    def login(uid):
        c.cookies.clear()
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "subscription_status": "active"}).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c

    c.login = login
    return c


REPORT = {"kind": "render", "message": "Cannot read properties of undefined (reading 'id')",
          "stack": "TypeError: Cannot read properties of undefined\n    at UploadScreen",
          "where": "uploads"}


def test_a_report_is_logged_as_a_warning_with_who_where_and_what(client, rec):
    c = client.login("u1")
    r = c.post("/client-error", json=REPORT,
               headers={"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 26_6) CriOS/155"})
    assert r.status_code == 204
    [(level, kw)] = rec.reports()
    # A warning, not an error: one browser's report is not proof of a bug on
    # its own, and a warning only emails when it bursts (many at once is a
    # deploy that broke a screen for everybody).
    assert level == "warning"
    assert kw["user_id"] == "u1" and kw["kind"] == "render" and kw["where"] == "uploads"
    assert kw["message"].startswith("Cannot read properties")
    assert "UploadScreen" in kw["stack"] and "iPhone" in kw["ua"]


def test_every_field_is_cut_short(client, rec):
    c = client.login("u1")
    c.post("/client-error", json={"kind": "k" * 99, "message": "m" * 999,
                                  "stack": "s" * 5000, "where": "w" * 99})
    [(_, kw)] = rec.reports()
    assert (len(kw["kind"]), len(kw["message"]), len(kw["stack"]), len(kw["where"])) == (20, 300, 1500, 40)


def test_a_stranger_cannot_write_to_the_journal(client, rec):
    client.cookies.clear()
    r = client.post("/client-error", json=REPORT, headers={"accept": "application/json"})
    assert r.status_code == 401
    assert rec.reports() == []


def test_an_oversized_body_is_refused_before_it_is_parsed(client, rec):
    c = client.login("u1")
    big = json.dumps({**REPORT, "stack": "x" * 20_000})
    r = c.post("/client-error", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert rec.reports() == []


def test_an_oversized_body_that_lies_about_its_length_is_refused_too(client, rec):
    """The declared length is the client's word; the running total is not."""
    c = client.login("u1")

    def chunks():
        yield b'{"message": "'
        for _ in range(40):
            yield b"x" * 1024
        yield b'"}'
    r = c.post("/client-error", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert rec.reports() == []


def test_garbage_is_a_400_and_logs_nothing(client, rec):
    c = client.login("u1")
    assert c.post("/client-error", content=b"not json",
                  headers={"content-type": "application/json"}).status_code == 400
    assert c.post("/client-error", json=["a", "list"]).status_code == 400
    assert rec.reports() == []


def test_a_crash_loop_is_capped_per_account_and_the_window_resets(client, rec, monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(api.time, "time", lambda: clock[0])
    c = client.login("u1")
    for i in range(api._CLIENT_ERROR_MAX + 5):
        assert c.post("/client-error", json={**REPORT, "message": f"boom {i}"}).status_code == 204
    assert len(rec.reports()) == api._CLIENT_ERROR_MAX
    # Somebody else's reports are not eaten by u1's loop.
    client.login("u2").post("/client-error", json=REPORT)
    assert len(rec.reports()) == api._CLIENT_ERROR_MAX + 1
    # And u1 is heard again once the window has passed.
    clock[0] += api._CLIENT_ERROR_WINDOW + 1
    client.login("u1").post("/client-error", json=REPORT)
    assert len(rec.reports()) == api._CLIENT_ERROR_MAX + 2


def test_the_limiter_forgets_expired_accounts(client, rec, monkeypatch):
    """One entry per account that ever reported, so it is swept, not grown."""
    clock = [1_000_000.0]
    monkeypatch.setattr(api.time, "time", lambda: clock[0])
    stale = {f"gone{i}": (1, clock[0] - api._CLIENT_ERROR_WINDOW - 5) for i in range(1001)}
    api._client_error_hits.update(stale)
    client.login("u1").post("/client-error", json=REPORT)
    assert set(api._client_error_hits) == {"u1"}
