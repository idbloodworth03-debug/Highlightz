"""Signed in must look signed in (audit, 2026-11-21).

Users reported being signed out over and over. The server was not ending their
sessions: the site was telling signed-in people they were signed out.

  1. /login showed the sign-in form to anyone, signed in or not. A bookmark, a
     home-screen icon, the "Sign in" on a public page, or Back after signing in
     (Twitch replays its old answer, and the callback lands on
     /login?error=invalid_state) all showed a signed-in person the form.
  2. Every public page's bar said "Sign in" and "Get started" to everybody,
     with no way back to the dashboard. The dashboard's own Tutorial links to
     one of those pages.
  3. A sign-in lasted 7 days from the last visit, so anyone who came back less
     than weekly had to sign in again every time. Now 30.

What these pin: a signed-in visitor is sent to the dashboard from /login (with
or without an error, with `next` honoured) and is offered "Dashboard" on every
public page; a signed-out visitor sees exactly what they saw before; a session
whose account was deleted cannot loop between /login and /; and the cookie
lasts 30 days.
"""

import json
import re

import pytest
from fastapi.testclient import TestClient

from src.auth import users as user_store
from src.dashboard import api, funnel, site_nav

NAV_HEADERS = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
               "sec-fetch-dest": "document", "sec-fetch-mode": "navigate"}
PASSWORD = "stays-signed-in-pw-1"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api, "_login_attempts", {})
    monkeypatch.setattr(api, "_pw_slow_spent", [])
    monkeypatch.setattr(api, "_pw_waiting", 0)
    monkeypatch.setattr(api, "_pw_gate", None)
    monkeypatch.setattr(api, "_pw_gate_loop", None)
    monkeypatch.setattr(funnel, "_PATH", tmp_path / "funnel.json")
    funnel._counts.clear()
    funnel._once.clear()
    funnel._loaded = True
    yield tmp_path
    funnel._counts.clear()


@pytest.fixture
def anon(store):
    return TestClient(api.app, base_url="https://testserver")


@pytest.fixture
def member(store):
    user_store.create("staysin", PASSWORD)
    c = TestClient(api.app, base_url="https://testserver")
    r = c.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    return c


# ── 1. /login ────────────────────────────────────────────────────────────────

def test_a_signed_in_person_opening_the_sign_in_page_goes_to_their_dashboard(member):
    r = member.get("/login", headers=NAV_HEADERS, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"


@pytest.mark.parametrize("error", ["invalid_state", "twitch_failed", "kick_failed", "incorrect_password"])
def test_an_error_in_the_address_does_not_show_a_signed_in_person_the_form(member, error):
    """Back after signing in replays Twitch's old answer: invalid_state, for
    somebody who is signed in. Whatever failed, they are signed in."""
    r = member.get(f"/login?error={error}", headers=NAV_HEADERS, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"


def test_next_is_still_honoured_for_a_signed_in_person(member):
    r = member.get("/login?next=promote", headers=NAV_HEADERS, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == api._after_sign_in("promote")


def test_the_redirect_is_not_counted_as_someone_seeing_the_sign_in_page(member):
    member.get("/login", headers=NAV_HEADERS, follow_redirects=False)
    assert funnel.totals()["totals"]["login_view"] == 0


def test_a_signed_out_visitor_still_gets_the_form_and_is_counted(anon):
    r = anon.get("/login", headers=NAV_HEADERS, follow_redirects=False)
    assert r.status_code == 200
    assert "/auth/twitch" in r.text
    assert funnel.totals()["totals"]["login_view"] == 1


def test_a_signed_out_visitor_still_sees_why_sign_in_failed(anon):
    r = anon.get("/login?error=invalid_state", headers=NAV_HEADERS, follow_redirects=False)
    assert r.status_code == 200
    assert api._ERROR_MESSAGES["invalid_state"] in r.text


def test_a_deleted_account_cannot_loop_between_sign_in_and_home(member, store):
    """/login trusts the session's own word; / checks the account and ends the
    session. So: /login -> / (landing, session over) -> /login (the form)."""
    (store / "users.json").write_text(json.dumps([]))
    user_store._snap = None
    r = member.get("/login", headers=NAV_HEADERS, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/"
    r = member.get("/", headers=NAV_HEADERS, follow_redirects=False)
    assert r.status_code == 200
    assert len(r.text) < len(api.DASHBOARD_HTML)            # the landing page, not the app
    r = member.get("/login", headers=NAV_HEADERS, follow_redirects=False)
    assert r.status_code == 200
    assert "/auth/twitch" in r.text


# ── 2. the public pages' bar ─────────────────────────────────────────────────

PUBLIC = ["/tos", "/privacy", "/cookies", "/tutorial", "/compare", "/blog", "/blog/how-clippers-make-money"]


@pytest.mark.parametrize("path", PUBLIC)
def test_a_public_page_offers_a_signed_in_person_the_dashboard(member, path):
    r = member.get(path, headers=NAV_HEADERS)
    assert r.status_code == 200, path
    assert site_nav.SIGN_IN not in r.text, path
    assert site_nav.GET_STARTED not in r.text, path
    assert site_nav.DASHBOARD_BTN in r.text, path


@pytest.mark.parametrize("path", PUBLIC)
def test_a_public_page_is_unchanged_for_a_signed_out_visitor(anon, path):
    r = anon.get(path, headers=NAV_HEADERS)
    assert r.status_code == 200, path
    assert site_nav.SIGN_IN in r.text, path
    assert site_nav.GET_STARTED in r.text, path
    assert "Dashboard</a>" not in r.text, path


def test_promote_keeps_its_campaign_button_and_offers_the_dashboard(member, monkeypatch):
    monkeypatch.setattr(api.settings, "campaign_requests_enabled", True)
    r = member.get("/promote", headers=NAV_HEADERS)
    assert r.status_code == 200
    assert site_nav.SIGN_IN not in r.text
    assert site_nav.DASHBOARD_LINK in r.text
    # its own button, to the campaign form (the & is written as &amp;)
    import html as _html
    assert f'href="{_html.escape(api._after_sign_in("promote"))}" class="btn btn-go">Get started' in r.text


def test_every_copy_of_the_bar_uses_the_markup_the_swap_looks_for():
    """The swap is a string replace, so a bar written differently would keep
    saying "Sign in" to signed-in people. Every copy must carry it."""
    import pathlib
    src = pathlib.Path(api.__file__).parent
    bars = 0
    for f in src.glob("*.py"):
        text = f.read_text()
        for m in re.finditer(r'<div class="nav-right">(.*?)</div>', text, re.S):
            bars += 1
            assert site_nav.SIGN_IN in m.group(1), f.name
    assert bars >= 6


# ── 3. how long a sign-in lasts ──────────────────────────────────────────────

def test_a_sign_in_lasts_30_days_from_the_last_visit(store):
    user_store.create("staysin", PASSWORD)
    c = TestClient(api.app, base_url="https://testserver")
    r = c.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert "Max-Age=2592000" in r.headers["set-cookie"]
    # and every later answer renews it
    r = c.get("/me")
    assert "Max-Age=2592000" in r.headers["set-cookie"]
