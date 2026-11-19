"""Free pauses after 2 hours without using the site (it was 8).

Owner, 2026-11-19: "do 2 hours for free", after the production check showed
Free accounts with room in their queue watched for hours with nobody there.
Held here: the number, and that it reaches only plans that pause; that a tab
left open and untouched counts as away (as it always did) and so the card
offers Resume; that Resume obeys every hold; and that nothing anywhere still
says 8 hours.
"""

import asyncio
import inspect
import re
import time

import pytest

from src.billing import plans
from src.dashboard import api
from tests.test_free_queue_pause import NOW, _events, scene  # noqa: F401  (the fixture)


def test_the_number_is_two_hours():
    assert api._IDLE_STREAM_TIMEOUT == 2 * 3600 and api.IDLE_PAUSE_HOURS == 2


def test_two_hours_without_using_the_site_pauses_a_free_account(scene):
    scene.person("away", last=time.time() - 2 * 3600 - 60, pending=3)
    scene.person("here", last=time.time() - 2 * 3600 + 120, pending=3)
    scene.tick()
    assert scene.rec("away")["status"] == "paused" and "hold" not in scene.rec("away")
    assert scene.rec("here")["status"] == "live"
    assert "streams_paused_idle" in _events(scene, "away")


@pytest.mark.parametrize("kw", [
    {"status": "active", "plan": "pro"}, {"status": "active", "plan": "studio"},
    {"status": "trialing", "trial_ends_at": NOW + 86400}, {"status": "none", "is_admin": True},
])
def test_a_plan_that_pays_for_watching_never_pauses_for_it(scene, kw):
    scene.person("p", last=time.time() - 30 * 3600, pending=3, **kw)
    scene.tick()
    assert scene.rec("p")["status"] == "live"
    assert plans.pauses_when_idle(scene.users["p"]) is False


def test_a_tab_left_open_and_untouched_counts_as_away(scene):
    """The keepalive is not activity, by design: an abandoned open tab must
    go idle. What changed is that at 2 hours somebody may be RIGHT THERE,
    so the card offers the way back (below)."""
    scene.person("tab", last=time.time() - 3 * 3600, pending=3, tab=True)
    api._user_last_active["tab"] = time.time() - 3 * 3600
    scene.tick()
    assert scene.rec("tab")["status"] == "paused"
    ws = inspect.getsource(api.websocket_endpoint)
    assert "deliberately do NOT bump _user_last_active" in ws


def _resume(uid):
    from starlette.requests import Request
    req = Request({"type": "http", "method": "POST", "path": "/streams/resume", "headers": [],
                   "session": {"auth": True, "user_id": uid}})
    return asyncio.run(api.resume_streams(req))


def test_resume_starts_a_channel_that_paused_for_going_quiet(scene, monkeypatch):
    monkeypatch.setattr(api, "_current_user_id", lambda request: "away")
    scene.person("away", last=time.time() - 3 * 3600, pending=3)
    scene.tick()
    scene.sent.clear()
    assert _resume("away") == {"resumed": 1}
    assert scene.rec("away")["status"] == "starting" and scene.started == [("chan", "away")]
    assert "stream_updated" in _events(scene, "away"), "the tab was not told it restarted"


@pytest.mark.parametrize("hold", ["billing", "queue_full"])
def test_resume_obeys_every_hold(scene, monkeypatch, hold):
    monkeypatch.setattr(api, "_current_user_id", lambda request: "held")
    scene.person("held", pending=plans.PLAN_LIMITS["free"]["max_pending"])
    api._streams["held:chan"].update(status="paused", hold=hold)
    api._paused_users.add("held")
    if hold == "queue_full":
        api._queue_held.add("held")
    assert _resume("held") == {"resumed": 0}
    assert scene.rec("held")["status"] == "paused" and scene.rec("held")["hold"] == hold


def test_the_card_offers_resume_only_for_a_channel_paused_for_going_quiet():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    card = page[page.index("function RdStream("):page.index("function RdStream(") + 9000]
    assert "const idlePaused = s.status==='paused' && !s.hold;" in card
    assert "{idlePaused &&" in card and "fetch('/streams/resume', {method:'POST'})" in card
    assert "{resuming ? 'Starting…' : 'Resume watching'}" in card
    # On its own line, not in the top row where it squeezed the channel name.
    top = card[card.index('<div className="rd-stream-actions">'):card.index('<div className="rd-stream-actions">') + 400]
    assert "resume" not in top.lower()
    # It calls no hook conditionally and returns nothing early before its hooks.
    body = card[:card.index("\n  return (\n")]
    assert body.count("useState(") >= 3
    # No early return at the component's own level before its hooks.
    assert not any(line.startswith("  if") and "return" in line for line in body.splitlines()), \
        "a hook after an early return breaks the card"


def test_the_toast_and_the_labels_say_two_hours_and_how_to_come_back():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert ("flash('Paused after 2 hours without you using Highlightz. Press Resume watching to start again. "
            "Paid plans keep watching while you are away.')") in page
    # On a phone a long toast wraps: a rounded box, not a pill drawn as a circle.
    assert "max-width:90vw;text-align:center;border-radius:var(--r-lg)}" in page
    assert page.count("'paused · 2 hours away'") == 2


def test_nothing_anywhere_still_says_eight_hours():
    """Every page a person reads, the brief, both admin pages and the check."""
    from fastapi.testclient import TestClient
    from pathlib import Path
    from src.dashboard.aurora_html import DASHBOARD_HTML
    brief = TestClient(api.app).get("/llms.txt").text
    for name, text in (("landing", api.LANDING_HTML), ("dashboard", DASHBOARD_HTML), ("llms.txt", brief),
                       ("admin", api.ADMIN_HTML), ("admin feedback", api._ADMIN_FEEDBACK_HTML),
                       ("reaper_check", Path("scripts/reaper_check.py").read_text())):
        found = re.findall(r".{0,60}(?:\b8 hours|\b8-hour|eight hours|away 8).{0,40}", text)
        found = [f for f in found if "until 2026-11-19" not in f]
        assert not found, f"{name}: {found[:2]}"
    assert "2 hours have passed without you using the dashboard" in api.LANDING_HTML
    assert "On Free, monitoring pauses after 2 hours without you using the dashboard" in api.LANDING_HTML
    assert "pauses after 2 hours without the dashboard being used" in brief


def test_the_check_uses_the_sites_number():
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("reaper_check", Path("scripts/reaper_check.py"))
    R = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(R)
    assert R.IDLE_FOR == api._IDLE_STREAM_TIMEOUT and R.IDLE_WORDS == "2 hours"
