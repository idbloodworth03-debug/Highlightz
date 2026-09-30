"""TikTok is only ever posted by a person, from its posting screen.

TikTok's Direct Post rules put the choices (who can view, comments/duets/
stitches, commercial-content disclosure, the music-usage agreement) on the
person for EACH post, and the app submission says nothing is posted until they
press Post. So nothing automatic may post there: not Autopilot, not the
Scheduler's due-time worker, not a retry, not a "Post now" on a queue card.

What these defend:
  1. `poster.auto_platforms` skips TikTok unless the item carries the person's
     own TikTok choices (`options["tiktok"]`, set only by /publish/post-now);
  2. Autopilot's saved config can no longer list TikTok (old configs are cleaned);
  3. the "time to post" nudge still reaches the person for a scheduled TikTok;
  4. the screens no longer offer TikTok as automatic.
"""

import time

import pytest

from src import autopilot as ap
from src.publish import connections, poster, schedule as sched


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(connections, "_INDEX", tmp_path / "publish_connections.json")
    connections._conns.clear(); connections._loaded = False
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json")
    sched._items.clear(); sched._loaded = False
    for p in ("tiktok", "instagram"):
        connections.save(connections.Connection(
            user_id="u1", platform=p, account_id="a", account_name="Nova",
            access_token="AT", refresh_token="RT"))
    yield
    connections._conns.clear(); connections._loaded = False
    sched._items.clear(); sched._loaded = False


def _item(**kw):
    return sched.add("u1", "up1", "clip.mp4", "cap", ["tiktok", "instagram"],
                     time.time() - 5, **kw)


def test_a_scheduled_item_is_never_posted_to_tiktok_automatically():
    assert poster.auto_platforms(_item()) == ["instagram"]


def test_an_autopilot_item_is_never_posted_to_tiktok_automatically():
    assert poster.auto_platforms(_item(source="autopilot")) == ["instagram"]


def test_tiktok_only_posts_when_the_person_chose_its_options():
    opts = {"tiktok": {"privacy_level": "SELF_ONLY", "consent": True}}
    assert poster.auto_platforms(_item(options=opts)) == ["tiktok", "instagram"]


def test_a_tiktok_only_item_has_nothing_to_post_automatically():
    it = sched.add("u1", "up1", "clip.mp4", "cap", ["tiktok"], time.time() - 5)
    assert poster.auto_platforms(it) == []


def test_autopilot_config_drops_tiktok():
    c = ap.normalize({"platforms": ["tiktok", "youtube", "instagram"]})
    assert c["platforms"] == ["youtube", "instagram"]
    assert "tiktok" not in ap.AUTO_PLATFORMS


def test_the_due_nudge_still_reaches_a_scheduled_tiktok():
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.schedule_due_task)
    assert 'auto_ok - {"tiktok"}' in src


def test_the_screens_do_not_offer_tiktok_as_automatic():
    from src.dashboard import aurora_html
    page = aurora_html.DASHBOARD_HTML
    ap_screen = page[page.index("function AutopilotScreen("):]
    ap_screen = ap_screen[:ap_screen.index("\nfunction ", 10)]
    assert "c.id !== 'tiktok'" in ap_screen
    assert "TikTok is posted by you, not by Autopilot" in ap_screen
    drawer = page[page.index("function ScheduleDrawer("):]
    drawer = drawer[:drawer.index("\nfunction ", 10)]
    assert "c.id !== 'tiktok'" in drawer
