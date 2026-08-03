"""Referral attribution — the thing the weekly table is built on.

Existing promo attribution fires from the STRIPE WEBHOOK at checkout, so it
records nothing for a free signup — and the growth plan is free signups. This
is the parallel path, and the failure modes are quiet ones: attribution that
silently moves to whoever posted most recently, or codes that work as a link
but not when typed.
"""

import time

import pytest

from src.auth import referrals


def test_a_link_and_a_typed_code_resolve_identically():
    """`?ref=tommy` from a bio and `TOMMY` typed into a DM are the same person
    doing the same outreach. Splitting them undercounts every lane that uses
    both, which is all of them."""
    assert referrals.normalise("tommy") == "tommy"
    assert referrals.normalise("TOMMY") == "tommy"
    assert referrals.normalise("  Tommy  ") == "tommy"
    assert referrals.normalise("THOMAS!") == "thomas"


def test_unknown_codes_are_dropped_rather_than_stored():
    """A stored typo becomes a row in the weekly report attributed to nobody,
    which reads as a real lane that produced users."""
    for junk in ("nobody", "", None, "x" * 200, "../../etc/passwd", "<script>"):
        assert referrals.normalise(junk) is None


def test_every_person_running_outreach_has_a_working_code():
    for who in ("ian", "andrew", "tommy", "thomas"):
        assert referrals.normalise(who) == who, f"{who} has no working code"


def test_first_touch_wins_and_is_permanent(tmp_path, monkeypatch):
    """Someone who arrives through Tommy's link, comes back through Ian's and
    then subscribes still counts as Tommy's. Without this, whoever posts most
    recently harvests everyone else's work."""
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")

    u = users.create("dave", "hunter2hunter2")
    assert users.set_ref_once(u["id"], "tommy") is True
    assert users.set_ref_once(u["id"], "ian") is False, "attribution was overwritten"
    assert users.get_by_id(u["id"])["ref"] == "tommy"


def test_setting_a_ref_on_a_missing_user_is_not_an_error(tmp_path, monkeypatch):
    from src.auth import users
    monkeypatch.setattr(users, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(users, "_BACKUP_FILE", tmp_path / "users.json.bak")
    assert users.set_ref_once("ghost", "ian") is False


# ── through the app ──────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    return TestClient(api.app)


def test_the_landing_page_stores_a_ref_in_the_session(client):
    """The session cookie is the ONLY thing that survives the trip to
    twitch.tv and back."""
    r = client.get("/?ref=tommy")
    assert r.status_code == 200
    assert client.cookies.get("session"), "no session cookie was set to carry the ref"


def test_ref_is_captured_on_every_public_entry_point():
    """A bio link might point at any of them."""
    import inspect
    from src.dashboard import api
    for fn in (api.dashboard, api.login_page, api.twitch_login):
        assert "_capture_ref(request)" in inspect.getsource(fn), \
            f"{fn.__name__} drops the referral"


def test_the_callback_reads_the_ref_before_clearing_the_session():
    """session.clear() for session fixation runs three lines from the
    attribution. Reading after it silently attributes nobody, and the bug is
    invisible — signups just all show up as Direct."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.twitch_callback)
    read_at = src.index('pending_ref = request.session.get("ref")')
    clear_at = src.index("request.session.clear()")
    assert read_at < clear_at, "the ref is read after the session is wiped"


def test_the_landing_page_advertises_the_free_plan():
    """Every signup from the outreach accounts arrives here. Advertising
    $10/$25 with no free option contradicts the product they are signing up
    for."""
    from src.dashboard.api import LANDING_HTML
    assert "Start free" in LANDING_HTML
    assert "no card" in LANDING_HTML.lower()
    assert "Monitor <b>1 stream</b>" in LANDING_HTML
    assert "15 pending clips" in LANDING_HTML


def test_the_structured_data_matches_what_is_actually_offered():
    """A price in schema.org that does not match the page is exactly what
    structured-data penalties exist for."""
    import json
    from src.dashboard.api import LANDING_HTML
    blob = LANDING_HTML[LANDING_HTML.index('{"@context"'):]
    offers = json.loads(blob[:blob.index("</script>")])["offers"]
    assert offers["lowPrice"] == "0.00", "still claims the cheapest plan costs money"
    assert offers["offerCount"] == "3"


def test_no_page_still_says_the_product_starts_at_ten_dollars():
    from src.dashboard.api import LANDING_HTML
    assert "From $10/month" not in LANDING_HTML
    assert "From <b>$10/month</b>" not in LANDING_HTML
