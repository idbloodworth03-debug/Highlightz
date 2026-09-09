"""
What happens to a tab whose session has expired.

THE FAILURE THIS FIXED, and it was silent in both directions. The dashboard is
a single-page app that people leave open for hours while streams run, and it
reaches the server through `fetch`. Browser fetch sends `Accept: */*` unless
told otherwise, and the auth middleware answered anything that was not
explicitly `application/json` with a 302 to /login — which fetch follows. So
when a tab outlived its session:

  * every poll came back as a whole HTML sign-in page, which the app tried to
    parse as its clip list; it threw, the call site's .catch() swallowed it,
    and the screen simply stopped updating with nothing saying why;
  * and each of those redirects landed on /login, where the funnel counted it
    as a person arriving at the sign-in page. One refetchAll from one stale
    tab produced four phantom visitors and no landing view, which is what made
    the funnel show people reaching sign-in without ever seeing the site.

Both halves are pinned here: the server must answer a script with an error it
can act on, and it must still answer a PERSON with the page. Getting the
second one wrong would show raw JSON to somebody who typed a URL, which is why
the detection fails toward the redirect rather than toward the 401.
"""

import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api, funnel


# A protected path — anything the middleware guards will do.
GUARDED = "/clips"

# What a browser actually sends, in the three shapes that reach this code.
FETCH_HEADERS = {"accept": "*/*", "sec-fetch-dest": "empty",
                 "sec-fetch-mode": "cors"}
NAV_HEADERS = {"accept": "text/html,application/xhtml+xml,application/xml;q=0.9",
               "sec-fetch-dest": "document", "sec-fetch-mode": "navigate"}
LEGACY_XHR = {"accept": "*/*", "x-requested-with": "XMLHttpRequest"}


@pytest.fixture
def anon(tmp_path, monkeypatch):
    """A signed-out client. https because the session cookie is Secure."""
    monkeypatch.setattr(funnel, "_PATH", tmp_path / "funnel.json")
    funnel._counts.clear()
    funnel._once.clear()
    funnel._loaded = True
    yield TestClient(api.app, base_url="https://testserver")
    funnel._counts.clear()


# ── a script gets an error it can act on ─────────────────────────────────────

def test_a_fetch_from_an_expired_tab_gets_a_401_not_a_login_page(anon):
    r = anon.get(GUARDED, headers=FETCH_HEADERS)
    assert r.status_code == 401
    assert r.json()["detail"]
    # Not an HTML page pretending to be data.
    assert "<!DOCTYPE" not in r.text


def test_an_explicit_json_client_still_gets_a_401(anon):
    """The original behaviour, kept. Anything already asking for JSON was
    always answered this way and must continue to be."""
    r = anon.get(GUARDED, headers={"accept": "application/json"})
    assert r.status_code == 401


def test_a_legacy_xhr_gets_a_401(anon):
    """Browsers too old for Sec-Fetch-Dest but new enough to set this."""
    r = anon.get(GUARDED, headers=LEGACY_XHR)
    assert r.status_code == 401


def test_the_app_is_never_handed_html_where_it_expects_json(anon):
    """The actual bug, stated as the app experiences it: refetchAll asks four
    endpoints, and every answer has to be something it can branch on."""
    for path in ("/clips", "/streams", "/profiles", "/me"):
        r = anon.get(path, headers=FETCH_HEADERS)
        assert r.status_code == 401, path
        assert r.headers["content-type"].startswith("application/json"), path


# ── a person still gets the page ─────────────────────────────────────────────

def test_a_person_typing_a_url_still_gets_the_sign_in_page(anon):
    """THE REGRESSION THIS GUARDS. Detecting API calls too eagerly would show
    raw JSON to somebody who followed a link, on the main auth path."""
    r = anon.get(GUARDED, headers=NAV_HEADERS)
    assert r.status_code == 200
    assert r.url.path == "/login"


def test_an_unrecognised_client_gets_the_page_rather_than_json(anon):
    """FAILS TOWARD THE REDIRECT. A client sending neither Sec-Fetch headers
    nor an Accept we recognise is treated exactly as it was before this
    existed — no worse off, and never shown raw JSON by mistake."""
    r = anon.get(GUARDED, headers={"accept": "*/*"})
    assert r.status_code == 200
    assert r.url.path == "/login"


def test_a_signed_out_visitor_still_sees_the_landing_page(anon):
    """The root path is public and must not be caught by any of this."""
    r = anon.get("/", headers=NAV_HEADERS)
    assert r.status_code == 200
    assert r.url.path == "/"


# ── and the funnel stops counting robots as people ───────────────────────────

def test_a_stale_tab_no_longer_registers_as_someone_reaching_sign_in(anon):
    """One refetchAll used to add four sign-in page views and no landing view,
    which is exactly the shape that made the funnel unreadable."""
    for path in ("/clips", "/streams", "/profiles", "/me"):
        anon.get(path, headers=FETCH_HEADERS)
    assert funnel.totals()["totals"]["login_view"] == 0


def test_a_real_visit_to_the_sign_in_page_is_still_counted(anon):
    """The counter must not have been fixed by switching it off."""
    anon.get("/login", headers=NAV_HEADERS)
    assert funnel.totals()["totals"]["login_view"] == 1


def test_a_person_bounced_from_a_protected_page_is_still_counted(anon):
    """They genuinely did arrive at the sign-in page — a bookmark to the
    dashboard from an expired session is a real person who needs to sign in,
    and dropping them would undercount the other way."""
    anon.get(GUARDED, headers=NAV_HEADERS)
    assert funnel.totals()["totals"]["login_view"] == 1
