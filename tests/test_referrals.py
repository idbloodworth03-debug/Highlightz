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
    assert "Monitor <b>1 channel</b>" in LANDING_HTML
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


def test_pricing_bullets_are_not_laid_out_as_flex_columns():
    """`display:flex` on .price-list .li makes every inline <b> its OWN flex
    column, so "Monitor up to <b>3 streams</b> at once" rendered as three
    stacked columns and "VOD Scanner" broke away from its own sentence. It only
    became visible when the cards narrowed to fit three across.

    The geometry is verified in the browser (scratchpad/drive_price.js); this
    just stops the declaration coming back.
    """
    from src.dashboard.api import LANDING_HTML
    i = LANDING_HTML.index(".price-list .li{")
    rule = LANDING_HTML[i:LANDING_HTML.index("}", i)]
    assert "display:flex" not in rule, \
        "pricing bullets are flex again — inline <b> will break the sentence"
    assert "position:relative" in rule and "padding-left" in rule, \
        "the tick needs absolute positioning for the text to be one inline flow"


def test_the_vod_scanner_is_named_consistently():
    from src.dashboard.api import LANDING_HTML
    assert "<b>VOD Scanner</b>" in LANDING_HTML
    assert "VOD scanner" not in LANDING_HTML, "mixed capitalisation of the feature name"


# ── short links ──────────────────────────────────────────────────────────────

def test_a_bare_slug_attributes_and_lands_on_the_page(client):
    """`highlightz.app/tommy` — a bio field shows whatever URL you type, so the
    ref cannot be hidden outright, but a bare path reads as a page rather than
    as tracking."""
    r = client.get("/tommy", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    assert client.cookies.get("session"), "the ref was not carried into a session"


def test_the_prefixed_form_works_too(client):
    r = client.get("/r/andrew", follow_redirects=False)
    assert r.status_code == 302


def test_short_links_work_signed_out(client):
    """The whole point is that a stranger clicks them. If AuthMiddleware
    bounced them to /login the ref would be gone before any handler ran."""
    r = client.get("/thomas", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" not in r.headers.get("location", "")


def test_an_unknown_slug_is_never_treated_as_a_referral():
    """The bare-slug route must behave as if it does not exist for anything
    that is not a referrer, or it shadows every future single-segment page.

    Signed OUT, an unknown path is a redirect to /login — that is the auth
    middleware doing its long-standing job, not this route. The tell is WHERE
    it redirects: this route always sends you to "/". Signed in, the middleware
    steps aside and the route must 404.
    """
    import base64, json as _j
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    from src.dashboard import api

    c = TestClient(api.app)
    out = c.get("/pricing", follow_redirects=False)
    assert out.headers.get("location", "").startswith("/login"), \
        f"an unknown slug was swallowed by the referral route: {out.headers.get('location')}"

    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "u", "subscription_status": "none"}).encode())).decode())
    assert c.get("/pricing", follow_redirects=False).status_code == 404


def test_the_bare_slug_route_does_not_shadow_real_pages():
    """Registration order is what keeps this safe, so assert it rather than
    trusting it: every real route is declared before the catch-all."""
    from src.dashboard import api
    paths = [getattr(r, "path", "") for r in api.app.routes]
    assert paths[-1] == "/{slug}", "the catch-all is no longer last"
    slug_at = paths.index("/{slug}")
    for real in ("/login", "/me", "/clips", "/streams", "/admin", "/tos", "/privacy"):
        assert paths.index(real) < slug_at, f"{real} is shadowed by the catch-all"


def test_real_pages_still_resolve_with_the_catch_all_in_place(client):
    """The direct proof, not just ordering: these must not 302 to '/'."""
    for path in ("/login", "/tos", "/privacy"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 200, f"{path} was captured by the catch-all"


def test_short_links_redirect_temporarily_not_permanently(client):
    """A 301 gets cached by the browser, so /tommy would keep bouncing that
    person to the landing page long after they signed in."""
    assert client.get("/tommy", follow_redirects=False).status_code == 302
