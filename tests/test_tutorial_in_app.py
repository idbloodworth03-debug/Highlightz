"""The walkthrough, readable inside the dashboard.

It used to be a link to /tutorial with target="_blank", because the dashboard is
a long-lived SPA holding a live socket and navigating away throws that state
out. That worked, and it meant reading instructions about a screen in a window
that was not that screen. It now has its own tab.

THE INVARIANT THAT MATTERS MOST is that there is still ONE tutorial. The public
page and the in-app tab render the same dataclasses; the moment anybody
transcribes the copy into the React component instead, the two start drifting
and a walkthrough that disagrees with itself is worse than one that is old.
"""

import pytest

from src.dashboard import tutorial_content as C


# ── one source, two renderers ────────────────────────────────────────────────

def test_the_in_app_payload_is_built_from_the_same_content_as_the_page():
    d = C.as_dict()
    assert d["hero"]["title"] == C.HERO_TITLE
    assert d["hero"]["lead"] == C.HERO_LEAD
    assert len(d["quickstart"]["sections"]) == len(C.QUICKSTART)
    assert len(d["features"]) == len(C.FEATURES)
    assert len(d["faq"]["items"]) == len(C.FAQ)
    assert len(d["plans"]["rows"]) == len(C.PLAN_ROWS)


def test_every_section_survives_serialisation_with_its_steps_and_tip():
    d = C.as_dict()
    by_id = {s["id"]: s for s in d["quickstart"]["sections"] + d["features"]}
    for src in C.QUICKSTART + C.FEATURES:
        got = by_id[src.id]
        assert got["title"] == src.title
        assert tuple(got["steps"]) == src.steps
        assert got["tip"] == src.tip
        assert got["note"] == src.note
        assert got["plan"] == src.plan


def test_the_component_does_not_carry_its_own_copy_of_the_words():
    """The drift guard: if the PROSE is in the dashboard source, somebody has
    transcribed the tutorial instead of rendering it, and the two will drift.

    Checks bodies and tips, not titles. Titles are things like "Live Streams"
    and "Settings" — quoted from the real UI on purpose, so they appear in the
    dashboard legitimately as nav labels, and asserting on them only catches
    the tutorial doing its job."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    prose = [C.HERO_LEAD, C.QUICKSTART_LEAD, C.FAQ_LEAD]
    prose += [s.body for s in C.QUICKSTART + C.FEATURES if s.body]
    prose += [s.tip for s in C.QUICKSTART + C.FEATURES if s.tip]
    prose += [a for _, a in C.FAQ]
    for text in prose:
        assert text not in DASHBOARD_HTML, \
            f"tutorial prose is hard-coded in the dashboard: {text[:60]!r}"


def test_media_paths_are_urls_not_bare_filenames():
    """The content file stores bare filenames because the page prefixes them.
    An in-app <img src="04-approve.png"> would resolve against the dashboard
    route and 404."""
    d = C.as_dict()
    media = [d["hero"]["media"]] + [s["media"] for s in d["features"] if s["media"]]
    for m in media:
        assert m["src"].startswith("/static/tutorial/"), m["src"]
        assert m["poster"].startswith("/static/tutorial/"), m["poster"]


def test_the_payload_says_whether_each_screenshot_actually_exists():
    """The web page draws a labelled placeholder for a missing screenshot
    rather than a broken-image icon. The in-app reader has to be able to make
    the same choice, or the two disagree about what an uncaptured slot is.

    Asserts the flag TRACKS REALITY, both ways round. The first version only
    checked it was a bool, and mutation testing walked past a hard-coded
    `True` — which would have drawn a broken-image icon for every screenshot
    nobody had captured yet."""
    d = C.as_dict()
    # A real, captured file.
    assert d["hero"]["media"]["exists"] is True, \
        "the hero screenshot is on disk but the payload says it is missing"
    # One that is definitely not on disk.
    ghost = C._media_dict(C.Media(src="99-not-captured-yet.png", alt="nothing here"))
    assert ghost["exists"] is False, \
        "a missing screenshot is reported as present — the reader gets a broken image"


def test_the_payload_is_json_serialisable():
    """It goes over the wire. A dataclass or a tuple left in it would 500 the
    endpoint, and only in production."""
    import json
    json.dumps(C.as_dict())


# ── the endpoint ─────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from src.dashboard import api
    return TestClient(api.app)


def test_the_endpoint_serves_the_walkthrough(client, monkeypatch):
    import base64, json as _j
    from itsdangerous import TimestampSigner
    from src.dashboard import api
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    client.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "u1", "username": "nova"}).encode())).decode())
    d = client.get("/tutorial/content").json()
    assert d["hero"]["title"] == C.HERO_TITLE
    assert len(d["features"]) == len(C.FEATURES)


def test_the_content_route_wins_against_the_slug_catch_all(client):
    """`/{slug}` matches any single-segment path and FastAPI resolves in
    declaration order — the trap /tutorial and /compare each carry a comment
    about. A two-segment path is not matched by it, but asserting the real
    resolution costs nothing and survives the routes being moved.

    Asserted against the ROUTE TABLE, not the source text. The first version of
    this compared string offsets and failed on a correctly-ordered file,
    because both /tutorial and /compare quote the literal `@app.get("/{slug}")`
    inside their docstrings — so index() found the warning about the trap
    rather than the trap."""
    from src.dashboard import api
    paths = [r.path for r in api.app.routes if hasattr(r, "path")]
    assert paths.index("/tutorial/content") < paths.index("/{slug}")
    assert paths.index("/tutorial") < paths.index("/{slug}")


def test_the_public_page_still_works(client):
    """Adding the in-app tab must not have taken the public one away — it is
    what a signed-out visitor reads, and what search indexes."""
    r = client.get("/tutorial")
    assert r.status_code == 200
    assert C.HERO_TITLE in r.text


# ── the tab, wired into the app ──────────────────────────────────────────────

def test_the_tab_exists_in_the_nav_and_has_a_screen_behind_it():
    """A nav entry with no route renders an empty screen; a route with no nav
    entry is unreachable. Both have happened here before."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert "{id:'tutorial',label:'Tutorial',icon:'book'}" in h
    assert "view==='tutorial'" in h
    assert "tutorial:['Tutorial'," in h, "the tab has no header"
    assert "book: <>" in h, "the nav icon does not exist and would render blank"


def test_the_walkthrough_is_pulled_in_refetchall():
    """Rule 3 of the realtime contract: anything fetched on mount is also
    pulled by refetchAll, so it self-heals after a reconnect or a deploy
    instead of going stale."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    i = h.index("const refetchAll = useCallback")
    body = h[i:h.index("const wsBootstrapped", i)]
    assert "/tutorial/content" in body, \
        "the walkthrough is not re-pulled on reconnect"


def test_the_empty_state_switches_tab_rather_than_leaving_the_app():
    """It was an <a target="_blank">. Navigating away in the SAME tab would
    drop the socket; a new tab kept it but split the reader across two windows.
    Switching route does neither."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert 'onClick={()=>onGoTutorial()}' in h
    assert 'onGoTutorial:()=>setRoute(\'tutorial\')' in h
    # The only remaining /tutorial link is the deliberate "open as a page" one.
    assert h.count('href="/tutorial"') == 1


def test_a_missing_payload_renders_a_wait_not_a_crash():
    """refetchAll has not returned yet on first paint, and a reader who clicks
    Tutorial in that second must not meet a blank screen."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert "Loading the walkthrough" in h


def test_the_scroll_spy_only_tracks_sections_that_are_in_the_rail():
    """Every quickstart step is a .tut-sec but none are TOC targets, so
    matching on all sections left the rail highlighting NOTHING for the first
    third of the page — exactly where a new reader is."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    i = h.index("function TutorialScreen(")
    body = h[i:h.index("const NAV=", i)]
    assert "const ids = toc.map" in body
    assert "for(const id of ids)" in body


def test_the_tab_is_reachable_while_kick_is_selected():
    """Kick blocks the clipping tabs while it is under construction. The
    walkthrough is global information, like Account and Feedback — blocking it
    would hide the explanation from the person most likely to be confused."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    i = h.index("const KICK_BLOCKED=")
    assert "'tutorial'" not in h[i:h.index("\n", i)]


def test_screenshots_are_lazy():
    """Eleven full-width screenshots eager-loading on tab open stalls the first
    paint of a screen whose whole job is to be read immediately.

    Read off the <img> TAG, not the surrounding source. Scoping to TutMedia is
    not enough — the comment above the tag quotes the attribute, so a search of
    the function body matched the explanation after the attribute itself had
    been deleted. Third time this exact shape of test has been wrong in this
    file's history: a source-string assertion cannot tell code from prose
    about code, so point it at the smallest span that contains only code."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    i = h.index('<img className="tut-media"')
    tag = h[i:h.index("/>", i)]
    assert 'loading="lazy"' in tag, \
        f"the tutorial screenshots load eagerly: {tag}"


# ── the copy the card cutover left behind ────────────────────────────────────

@pytest.mark.parametrize("dead", [
    "do not need a card",
    "on Free",
    "including Free",
])
def test_the_tutorial_no_longer_promises_the_old_terms(dead):
    """These survived the card cutover because the banned-phrase test looked
    for "credit card" and "free tier". "You do not need a card" is the same
    promise in different words, and it was about to be shown to every user on
    a tab in the app rather than on a page most never open."""
    from src.dashboard.tutorial_html import render
    assert dead not in render(), f"the tutorial still claims: {dead!r}"
    assert dead not in str(C.as_dict()), f"the in-app payload still claims: {dead!r}"


def test_the_tutorial_states_the_real_terms():
    from src.dashboard.tutorial_html import render
    page = render().lower()
    assert "card required" in page or "put a card down" in page
    assert "day 7" in page
