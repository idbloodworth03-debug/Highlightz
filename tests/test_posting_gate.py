"""A posting platform that is not open to an account is never posted to.

Owner, 2026-11-19: "make sure instagram is off for users since it is not
complete". Instagram is held by one setting (PUBLIC_PLATFORMS, default
"tiktok"; src/publish/release.py). Connecting a held platform was refused,
and so was "Post now". But the poster itself did not ask: an ordinary account
that ALREADY held a connection kept being posted to by the due-time worker,
Autopilot's queue, a retry and "Post" on a queue card. Held here:

  * the poster asks, in the one function all of those come through;
  * staff and early-access accounts are open to everything, so testing and
    Meta's own review keep working;
  * the dashboard does not name Instagram where it cannot know it is open;
  * scripts/posting_check.py reads what the server actually has.
"""

import asyncio
import importlib.util
import inspect
import pathlib
import time

import pytest

from config.settings import settings
from src.publish import connections, poster, providers, release, schedule as sched

USERS = {"ordinary": {"id": "ordinary", "username": "ordinary"},
         "tester": {"id": "tester", "username": "tester", "early_access": True},
         "boss": {"id": "boss", "username": "boss", "is_admin": True}}


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(connections, "_INDEX", tmp_path / "publish_connections.json")
    connections._conns.clear(); connections._loaded = False
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json")
    sched._items.clear(); sched._loaded = False
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: USERS.get(uid))
    monkeypatch.setattr(settings, "public_platforms", "tiktok")          # production's default
    poster._inflight.clear()
    for uid in list(USERS) + ["gone"]:
        for p in ("youtube", "instagram"):
            connections.save(connections.Connection(user_id=uid, platform=p, account_id="a",
                                                    account_name="acct", access_token="AT", refresh_token="RT"))
    yield
    connections._conns.clear(); connections._loaded = False
    sched._items.clear(); sched._loaded = False


def _item(uid, platforms=("instagram",)):
    return sched.add(uid, "up1", "clip.mp4", "cap", list(platforms), time.time() - 5)


def test_the_default_is_tiktok_only():
    assert type(settings).model_fields["public_platforms"].default == "tiktok"
    assert release.public_platforms() == ("tiktok",)
    assert release.open_for("instagram", USERS["ordinary"]) is False
    assert release.open_for("instagram", None) is False


def test_an_ordinary_account_holding_a_connection_is_not_posted_to():
    """The hole: the connection is there, the item names Instagram, and the
    due-time worker would have uploaded it."""
    it = _item("ordinary")
    assert "instagram" in connections.connected_platforms("ordinary")
    assert poster.auto_platforms(it) == []
    assert poster.start_now(it) is False
    assert asyncio.run(poster.post_item(it)) is None


def test_no_provider_is_ever_called_for_a_held_platform(monkeypatch):
    called = []

    class Prov:
        async def post(self, *a, **k):
            called.append(a)
            raise AssertionError("posted to a platform that is not open to this account")
    monkeypatch.setattr(providers, "get", lambda p: Prov())
    for platforms in (("instagram",), ("youtube", "instagram"), ("youtube",)):
        assert asyncio.run(poster.post_item(_item("ordinary", platforms))) is None
    assert called == []


def test_staff_and_early_access_accounts_still_post_which_is_how_it_is_tested():
    assert poster.auto_platforms(_item("tester")) == ["instagram"]
    assert poster.auto_platforms(_item("boss", ("youtube", "instagram"))) == ["youtube", "instagram"]


def test_the_day_it_is_made_public_it_posts_for_everybody(monkeypatch):
    it = _item("ordinary", ("youtube", "instagram"))
    monkeypatch.setattr(settings, "public_platforms", "tiktok,youtube")
    assert poster.auto_platforms(it) == ["youtube"]                 # YouTube open, Instagram still held
    monkeypatch.setattr(settings, "public_platforms", "tiktok,youtube,instagram")
    assert poster.auto_platforms(it) == ["youtube", "instagram"]


def test_an_account_that_cannot_be_looked_up_is_an_ordinary_one(monkeypatch):
    assert poster.auto_platforms(_item("gone")) == []
    from src.auth import users as user_store

    def broken(uid):
        raise OSError("disk")
    monkeypatch.setattr(user_store, "get_by_id", broken)
    assert poster.auto_platforms(_item("boss")) == [], "a failed lookup opened a held platform"


def test_everything_that_posts_comes_through_the_one_question():
    """The due-time worker, Autopilot's queue, a retry and both buttons."""
    src = inspect.getsource(poster)
    assert src.count("auto_platforms(item)") >= 3
    assert "and is_open(p)" in inspect.getsource(poster.auto_platforms)
    # Nothing else in the site calls a provider's post().
    root = pathlib.Path("src")
    callers = [str(f) for f in root.rglob("*.py")
               if ".post(" in f.read_text() and "providers" in f.read_text()
               and "await provider.post(" in f.read_text()]
    assert callers == ["src/publish/poster.py"], callers


def test_the_two_doors_that_were_already_shut_still_are():
    from src.dashboard import api
    connect = inspect.getsource(api.publish_connect)
    assert "if not release.open_for(platform, user_store.get_by_id(uid)):" in connect
    assert 'detail=f"Posting to {p.label} is coming soon."' in connect
    now = inspect.getsource(api.publish_post_now)
    assert "if not release.open_for(p, _me)" in now


def test_the_dashboard_does_not_name_instagram_where_it_cannot_know():
    page = pathlib.Path("src/dashboard/aurora_html.py").read_text()
    assert "Post this clip to TikTok, Instagram or YouTube now" not in page
    assert "then choose TikTok, Instagram or YouTube." not in page
    assert 'title="Post this clip now"' in page and "'Pick the clip, then choose where it goes.'" in page
    # What Autopilot and the Scheduler say about it comes from what is open.
    assert "soonLine: open.length && soon.length" in page


# ── scripts/posting_check.py ────────────────────────────────────────────────

_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "posting_check.py"
spec = importlib.util.spec_from_file_location("posting_check", _PATH)
C = importlib.util.module_from_spec(spec)
spec.loader.exec_module(C)

PEOPLE = [{"id": "a", "username": "alice", "email": "alice@private.example"},
          {"id": "b", "username": "bob"}, {"id": "t", "username": "tester", "early_access": True},
          {"id": "s", "username": "staff", "is_admin": True}]
CONNS = [{"user_id": "a", "platform": "instagram", "access_token": "ENC-SECRET"},
         {"user_id": "a", "platform": "tiktok"}, {"user_id": "b", "platform": "youtube"},
         {"user_id": "t", "platform": "instagram"}, {"user_id": "s", "platform": "instagram"},
         {"user_id": "deleted", "platform": "instagram"}, "junk"]
ITEMS = [{"user_id": "a", "platforms": ["instagram", "tiktok"], "status": "queued"},
         {"user_id": "a", "platforms": ["instagram"], "status": "posted"},
         {"user_id": "b", "platforms": ["instagram"], "status": "queued",
          "results": {"instagram": {"status": "posted"}}},
         {"user_id": "t", "platforms": ["instagram"], "status": "queued"}]


def test_the_check_counts_who_holds_what_against_what_is_open():
    got = C.survey(PEOPLE, CONNS, ITEMS, {"tiktok", "youtube"})
    assert got["staff"] == 1 and got["early"] == 1
    assert got["by_platform"] == {"instagram": (3, 1), "tiktok": (1, 1), "youtube": (1, 1)}
    assert got["holding"] == {"instagram": ["alice"]}        # not the tester, not staff, not the deleted account
    assert got["queued"] == {"instagram": 1}                 # not the posted ones, not the tester's


def _say(open_ids, started=1000.0, changed=500.0, has_rule=True, got=None):
    got = got or C.survey(PEOPLE, CONNS, ITEMS, set(open_ids))
    held = [p for p in ("youtube", "tiktok", "instagram") if p not in open_ids]
    return "\n".join(C.report(raw=",".join(open_ids), open_ids=open_ids, held_ids=held,
                              configured={"youtube": True, "tiktok": True, "instagram": True},
                              got=got, now=2000.0, started=started, changed=changed, has_rule=has_rule))


def test_the_check_says_off_when_it_is_off_and_names_who_still_holds_a_connection():
    out = _say(["tiktok", "youtube"])
    assert "open to ordinary accounts        : YouTube, TikTok" in out or "open to ordinary accounts        : TikTok, YouTube" in out
    assert "INSTAGRAM IS OFF FOR ORDINARY ACCOUNTS: it cannot be connected, chosen or posted to." in out
    assert "ORDINARY ACCOUNTS HOLDING INSTAGRAM (1)" in out and "    alice" in out
    assert "The 1 account(s) above still hold an old connection; nothing is posted to it." in out
    assert "queued posts naming Instagram for ordinary accounts: 1" in out
    for private in ("private.example", "ENC-SECRET", "bob", "tester\n"):
        assert private not in out


def test_the_check_says_so_when_the_switch_itself_is_open():
    out = _say(["tiktok", "instagram"])
    assert "INSTAGRAM IS OPEN TO EVERY ACCOUNT ON A PLAN THAT POSTS: PUBLIC_PLATFORMS names it." in out
    assert "IS OFF FOR ORDINARY ACCOUNTS" not in out


def test_the_check_says_when_the_running_site_does_not_have_the_rule_yet():
    out = _say(["tiktok"], started=1000.0, changed=1500.0)           # files written after it started
    assert "in the code on disk, NOT in the running site (it was not restarted)" in out
    assert "account that ALREADY holds a connection (1 such account(s) above)." in out
    assert C.DEPLOY in out
    assert C.DEPLOY in pathlib.Path("CLAUDE.md").read_text()
    assert "NOT in the code on disk" in _say(["tiktok"], has_rule=False)
    assert C.poster_has_the_rule() is True


# ── "the post button in general is not working" (owner, the same day) ───────
# Not reproduced: in a browser against the running app the button opens the
# dialog and the post goes through to the platform. What WAS found: a held
# platform could still be ticked by an account holding an old connection, and
# the post was then refused. And the server check now reads what happened to
# every post that was tried, because that is on the server and not here.

def test_every_picker_asks_the_same_question_and_a_held_platform_cannot_be_ticked():
    page = pathlib.Path("src/dashboard/aurora_html.py").read_text()
    assert "function canPostTo(c) { return !!(c && c.connected && !c.held && !c.last_error); }" in page
    assert "const ready = id => canPostTo(conn[id]) && (!camp || camp.platforms.includes(id));" in page
    assert page.count("filter(c=>canPostTo(c) && c.id !== 'tiktok')") == 2          # the Scheduler and Autopilot
    assert "if (canPostTo(c)) return (" in page                                      # the connected-accounts row
    # No picker is left asking only "connected and healthy".
    assert "c.connected && !c.last_error &&" not in page
    assert "conn[id].connected && !conn[id].last_error" not in page
    # In the dialog a held platform reads "Soon": no Connect, no Reconnect.
    dialog = page[page.index("function PostNowDialog("):page.index("function PostNowDialog(") + 12000]
    held = dialog.index("if (c && c.held) return (")
    assert held < dialog.index("<small>Reconnect</small>") and "<small>Soon</small>" in dialog[held:held + 400]


def test_the_server_tells_the_page_which_platforms_are_held_for_this_account():
    from src.dashboard import api
    src = inspect.getsource(api._connections_payload)
    assert "held = not release.open_for(p.id, user)" in src and '"held": held,' in src


NOW = 2_000_000_000.0
TRIED = [
    {"user_id": "a", "platforms": ["youtube"], "results": {
        "youtube": {"status": "failed", "at": NOW - 600, "error": "Your YouTube connection has expired. Connect it again."}}},
    {"user_id": "a", "platforms": ["tiktok"], "results": {
        "tiktok": {"status": "failed", "at": NOW - 7200,
                   "error": "TikTok refused: access_token act.AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 is invalid " + "x" * 200}}},
    {"user_id": "s", "platforms": ["youtube"], "results": {
        "youtube": {"status": "posted", "at": NOW - 3 * 86400, "url": "https://youtu.be/x"}}},
    {"user_id": "t", "platforms": ["instagram"], "results": {
        "instagram": {"status": "failed", "at": NOW - 9 * 86400, "error": "old"},
        "youtube": {"status": "posting", "at": NOW - 5}}},
    {"user_id": "a", "platforms": ["youtube"]}, "junk",
]
LINKS = [{"user_id": "a", "platform": "youtube", "last_error": "Your YouTube connection has expired. Connect it again."},
         {"user_id": "b", "platform": "youtube"}, {"user_id": "a", "platform": "tiktok"},
         {"user_id": "deleted", "platform": "tiktok", "last_error": "x"}]


def test_the_check_reads_what_happened_to_every_post_that_was_tried():
    got = C.attempts(TRIED, LINKS, PEOPLE, NOW)
    assert got["ever"] == 4                                   # the one in flight and the one never tried are not outcomes
    assert got["counts"]["day"] == {"youtube": (0, 1), "tiktok": (0, 1)}
    assert got["counts"]["week"] == {"youtube": (1, 1), "tiktok": (0, 1)}
    assert got["why"]["youtube"] == [("Your YouTube connection has expired. Connect it again.", 1)]
    assert [(r["platform"], r["ok"], r["who"]) for r in got["last"]] == [
        ("youtube", False, "ordinary"), ("tiktok", False, "ordinary"),
        ("youtube", True, "staff"), ("instagram", False, "early access")]
    assert got["healthy"] == {"youtube": 1, "tiktok": 1} and got["broken_n"] == {"youtube": 1}


def test_a_platforms_reason_is_printed_without_anything_shaped_like_a_key():
    text = C.clean("TikTok refused: access_token act.AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 is invalid\n  try again " + "y" * 300)
    assert "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789" not in text and "[...]" in text
    assert len(text) <= 113 and "\n" not in text
    assert C.clean(None) == ""


def test_the_posts_section_says_it_in_words_and_names_nobody():
    import collections
    got = C.attempts(TRIED, LINKS, PEOPLE, NOW)
    out = "\n".join(C.posts_report(got, NOW, collections.Counter({"post_failed": 2, "post_published": 0}), True, 3600.0))
    assert "last 24 hours : TikTok 0 posted, 1 failed;  YouTube 0 posted, 1 failed" in out
    assert "last 7 days   : TikTok 0 posted, 1 failed;  YouTube 1 posted, 1 failed" in out
    assert "why YouTube failed (last 7 days):" in out
    assert "       1 x  Your YouTube connection has expired. Connect it again." in out
    assert "connected accounts that can post / must reconnect first:" in out
    assert "    YouTube                        : 1 / 1" in out
    assert "10 min ago    YouTube    FAILED: Your YouTube connection has expired. Connect it again.   (ordinary account)" in out
    assert "         2  post_failed" in out and "post_published" not in out
    for private in ("alice", "bob", "private.example", "AbCdEfGhIjKl", "youtu.be"):
        assert private not in out
    nothing = "\n".join(C.posts_report(C.attempts([], [], PEOPLE, NOW), NOW, collections.Counter(), True, 600.0))
    assert "no queue item records an attempt" in nothing and "last 24 hours : nothing tried" in nothing
    assert "nothing: no post went out and none failed in that time" in nothing
    src = inspect.getsource(C.attempts)
    assert "username" not in src


def test_the_events_it_counts_are_ones_the_poster_logs():
    logged = (pathlib.Path("src/publish/poster.py").read_text()
              + pathlib.Path("src/publish/providers/tiktok.py").read_text())
    for name in C.POST_EVENTS:
        assert f'"{name}"' in logged, name
