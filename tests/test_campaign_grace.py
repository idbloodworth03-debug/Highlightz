"""A campaign's grace period, its clock, and platforms that are not open yet
(owner, 2026-10-25: "I want it to last 7 days with a 3 day grace period so
clips posted on the last day still have a chance to get views ... allow tiktok
and instagram").

What these defend:
  1. the grace period is optional, after the end, and at most 30 days; a
     campaign made before it existed has none and behaves as before;
  2. the status runs upcoming -> live -> counting -> ended, and counting only
     exists while a grace period runs;
  3. during it nobody can post for the campaign, entries posted after the end
     do not count, counts can still be typed in, and the winners cannot be
     confirmed until it is over;
  4. the clock tells every open tab when a status moves (start, end, end of
     the grace period), never on its first pass, and emails the owner once
     the winners can be picked;
  5. a campaign platform the viewer cannot post to from Highlightz yet
     (Instagram until Meta approves) is marked as such in the listing;
  6. the dashboard, the streamer's Promote card and the admin page show it.
"""

import asyncio
import base64
import json
import time

import pytest
from itsdangerous import TimestampSigner

from src import campaigns as camp
from src.campaigns import joins
from src.campaigns import requests as creq
from src.campaigns import results as res
from src.publish import connections, schedule as sched

NOW = time.time()
DAY = 86400
PEOPLE = {
    "boss": {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
    "ann": {"id": "ann", "username": "ann", "twitch_login": "ann_clips", "subscription_status": "active"},
    "bob": {"id": "bob", "username": "bob", "subscription_status": "active"},
}


def form(**kw):
    """The duxxion campaign: 7 days of posting, then 3 days of counting."""
    base = {"title": "Clip duxxion", "summary": "s", "rules": "r", "start_at": NOW - DAY,
            "end_at": NOW + 6 * DAY, "views_until": NOW + 9 * DAY,
            "streamers": [{"name": "duxxion", "platform": "twitch"}], "prize_pool": 500,
            "payouts": [250, 150, 100], "judging": "top_clip_views", "platforms": ["tiktok", "instagram"],
            "hashtags": "#duxxion", "published": True}
    base.update(kw)
    return base


def post(c, user, at):
    """A clip posted for the campaign through Highlightz, landed at `at`."""
    it = sched.Item(id=f"it{len(sched._items) + 1:04d}", user_id=user, upload_id="u", filename="f.mp4",
                    caption="#duxxion", platforms=["tiktok"], status="posted", campaign_id=c["id"])
    it.results = {"tiktok": {"status": "posted", "url": f"https://www.tiktok.com/@{user}/video/{len(sched._items)}",
                             "at": at}}
    sched._items[it.id] = it
    sched._save()
    return f"{it.id}:tiktok"


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(camp, "_INDEX", tmp_path / "campaigns.json")
    monkeypatch.setattr(camp, "_IMAGES", tmp_path / "campaign_images")
    monkeypatch.setattr(creq, "_INDEX", tmp_path / "campaign_requests.json")
    monkeypatch.setattr(res, "_INDEX", tmp_path / "campaign_results.json")
    monkeypatch.setattr(joins, "_INDEX", tmp_path / "campaign_joins.json")
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json")
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(user_store, "display_names", lambda ids: {
        i: PEOPLE[i].get("twitch_login") or PEOPLE[i]["username"] for i in ids if i in PEOPLE})
    monkeypatch.setattr(user_store, "version", lambda: 1)
    monkeypatch.setattr(user_store, "admin_ids", lambda: frozenset(i for i, p in PEOPLE.items() if p.get("is_admin")))
    stores = ((camp, camp._campaigns), (creq, creq._requests), (res, res._results), (sched, sched._items),
              (res, res._memo), (joins, joins._joins))
    for m, store in stores:
        store.clear()
        m._loaded = True
    yield
    for m, store in stores:
        store.clear()
        m._loaded = False


# ── 1. the field ─────────────────────────────────────────────────────────────

def test_a_grace_period_is_optional_after_the_end_and_at_most_30_days():
    c = camp.create(form())
    assert c["views_until"] == NOW + 9 * DAY
    for blank in (None, "", 0, "0"):
        assert camp.create(form(views_until=blank))["views_until"] == 0
    for bad, msg in ((NOW + 6 * DAY, "until after the campaign ends"), (NOW + 2 * DAY, "until after"),
                     (NOW + 6 * DAY + 31 * DAY, "at most 30 days"), ("soon", "Pick when views stop")):
        with pytest.raises(camp.CampaignError, match=msg):
            camp.create(form(views_until=bad))
    # An edit that moves the end past it is refused rather than silently dropping it.
    with pytest.raises(camp.CampaignError, match="until after"):
        camp.update(c["id"], form(end_at=NOW + 10 * DAY))


def test_a_campaign_from_before_grace_periods_behaves_as_before():
    c = camp.create(form(end_at=NOW - 3600, views_until=0))
    c.pop("views_until")                      # as stored by the code before this
    assert camp.status_of(c) == "ended" and camp.judged_from(c) == c["end_at"]
    assert camp.public(c)["views_until"] == 0


# ── 2. the status ────────────────────────────────────────────────────────────

def test_the_status_runs_upcoming_live_counting_ended():
    c = camp.create(form(start_at=NOW + DAY, end_at=NOW + 8 * DAY, views_until=NOW + 11 * DAY))
    assert [camp.status_of(c, NOW + d * DAY) for d in (0, 2, 8.5, 10.9, 11, 30)] == \
        ["upcoming", "live", "counting", "counting", "ended", "ended"]
    assert camp.judged_from(c) == NOW + 11 * DAY
    plain = camp.create(form(views_until=0))
    assert camp.status_of(plain, NOW + 6 * DAY + 1) == "ended", "no grace period, no counting"


def test_the_listing_puts_counting_after_upcoming_and_before_ended():
    camp.create(form(title="Old", start_at=NOW - 20 * DAY, end_at=NOW - 12 * DAY, views_until=NOW - 10 * DAY))
    camp.create(form(title="Counting", start_at=NOW - 8 * DAY, end_at=NOW - DAY, views_until=NOW + 2 * DAY))
    camp.create(form(title="Soon", start_at=NOW + DAY, end_at=NOW + 8 * DAY, views_until=0))
    camp.create(form(title="Now"))
    rows = camp.listing(include_drafts=True)
    assert [(r["title"], r["status"]) for r in rows] == [
        ("Now", "live"), ("Soon", "upcoming"), ("Counting", "counting"), ("Old", "ended")]


# ── 3. what the grace period allows ──────────────────────────────────────────

def test_during_it_entries_keep_counting_but_late_posts_do_not_and_winners_wait():
    c = camp.create(form(start_at=NOW - 8 * DAY, end_at=NOW - DAY, views_until=NOW + 2 * DAY))
    on_time, late = post(c, "ann", NOW - 2 * DAY), post(c, "bob", NOW - 3600)
    res.save_stats(c, [{"key": on_time, "views": 5000}, {"key": late, "views": 9000}], "boss")
    why = {e["key"]: e["why_not"] for e in res.entries(c)}
    assert why == {on_time: "", late: "posted outside the campaign dates"}
    assert [r["name"] for r in res.standings(c)] == ["ann_clips"]
    with pytest.raises(res.ResultsError, match="still counting"):
        res.confirm(c, res.propose(c), "boss")
    # The grace period over: the winners can be picked.
    c = camp.update(c["id"], form(start_at=NOW - 8 * DAY, end_at=NOW - DAY, views_until=NOW - 60))
    assert camp.status_of(c) == "ended"
    rec = res.confirm(c, res.propose(c), "boss")
    assert [(w["name"], w["amount"]) for w in rec["winners"]] == [("ann_clips", 250)]


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.auth import users as user_store
    from src.dashboard import api
    monkeypatch.setattr(user_store, "_load", lambda: [dict(u) for u in PEOPLE.values()])
    monkeypatch.setattr(user_store, "_save", lambda u: None)
    monkeypatch.setattr(api.settings, "campaigns_enabled", True)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": PEOPLE[uid].get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c
    c.login, c.api = login, api
    return c


def test_nobody_can_post_for_it_once_posting_closes(client, tmp_path, monkeypatch):
    monkeypatch.setattr(connections, "_INDEX", tmp_path / "publish_connections.json")
    connections._conns.clear(); connections._loaded = False
    monkeypatch.setattr(client.api.settings, "uploads_enabled", True)
    connections.save(connections.Connection(user_id="boss", platform="tiktok", account_id="a",
                                            account_name="Nova", access_token="AT", refresh_token="RT"))
    try:
        c = camp.create(form(start_at=NOW - 8 * DAY, end_at=NOW - DAY, views_until=NOW + 2 * DAY))
        r = client.login("boss").post("/publish/post-now", json={
            "clip_id": "c1", "platforms": ["tiktok"], "caption": "#duxxion", "campaign_id": c["id"]})
        assert r.status_code == 400 and "Posting for this campaign has closed" in r.json()["detail"]
        assert sched.for_campaign(c["id"]) == []
    finally:
        connections._conns.clear(); connections._loaded = False


# ── 4. the clock ─────────────────────────────────────────────────────────────

@pytest.fixture
def clock(monkeypatch):
    from src.dashboard import api
    sent, emails = [], []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api, "_tell_owner_email", lambda s, t: emails.append((s, t)))
    monkeypatch.setattr(api, "_campaign_phases", None)
    tick = lambda now: asyncio.run(api._campaign_clock_tick(now))
    return tick, sent, emails


def test_the_clock_tells_every_tab_each_time_a_status_moves(clock):
    tick, sent, emails = clock
    c = camp.create(form(start_at=NOW + DAY, end_at=NOW + 8 * DAY, views_until=NOW + 11 * DAY))
    k = post(c, "ann", NOW + 2 * DAY)
    assert tick(NOW) == [] and sent == [], "the first pass only records"
    assert tick(NOW + 60) == [] and sent == [], "nothing moved, nothing sent"
    for at, status in ((NOW + 1.5 * DAY, "live"), (NOW + 8.5 * DAY, "counting")):
        assert tick(at) == [c["id"]] and camp.status_of(c, at) == status
        assert sent.pop() == ("campaigns_changed", None) and not sent
    assert emails == [], "nobody is asked to pick winners while views still count"
    assert tick(NOW + 11 * DAY) == [c["id"]] and sent == [("campaigns_changed", None)]
    (subject, text), = emails
    assert subject == "Pick the winners: Clip duxxion" and "grace period" in text and "views are final" in text
    assert "1 entry from 1 clipper, 1 not counted yet" in text
    res.save_stats(c, [{"key": k, "views": 10}], "boss")
    assert tick(NOW + 12 * DAY) == [] and len(emails) == 1, "told once"


def test_the_owner_is_told_only_about_published_campaigns_with_prizes(clock):
    tick, sent, emails = clock
    draft = camp.create(form(title="Draft", published=False, views_until=0))
    camp.create(form(title="No prizes", payouts=[], prize_pool=0, views_until=0))
    plain = camp.create(form(title="Plain", views_until=0))
    tick(NOW)
    assert len(tick(NOW + 6 * DAY + 60)) == 3 and sent == [("campaigns_changed", None)], "one event for all"
    assert [s for s, _ in emails] == ["Pick the winners: Plain"]
    assert '"Plain" has ended.' in emails[0][1]
    assert draft["id"] in camp.statuses() and plain["id"] in camp.statuses()


def test_an_admin_edit_is_told_once_by_the_edit_and_the_clock_stays_quiet_on_creation(clock):
    tick, sent, _emails = clock
    tick(NOW)
    camp.create(form())                       # the create endpoint broadcasts this itself
    assert tick(NOW + 30) == [] and sent == []


def test_the_main_process_runs_the_clock():
    src = open("src/main.py", encoding="utf-8").read()
    assert "dashboard_api.campaign_clock_task()" in src


# ── 5. platforms that are not open yet ───────────────────────────────────────

def test_the_listing_marks_platforms_the_viewer_cannot_post_to_yet(client, monkeypatch):
    c = camp.create(form())
    monkeypatch.setattr(client.api.settings, "public_platforms", "tiktok")
    held = lambda who: next(r for r in client.login(who).get("/campaigns").json()["rows"]
                            if r["id"] == c["id"])["held"]
    assert held("ann") == ["instagram"], "Instagram is not open to ordinary accounts yet"
    assert held("boss") == [], "admins can post everywhere (testing, Meta's reviewer)"
    monkeypatch.setattr(client.api.settings, "public_platforms", "tiktok,instagram")
    assert held("ann") == [], "the day Meta approves, nothing else has to change"


# ── 6. what people see ───────────────────────────────────────────────────────

def test_the_streamer_s_card_carries_the_grace_period():
    c = camp.create(form(start_at=NOW - 8 * DAY, end_at=NOW - DAY, views_until=NOW + 2 * DAY))
    d = creq.public({"id": "r1", "user_id": "streamr", "judging": "top_clip_views", "campaign_id": c["id"]})
    assert d["campaign"]["status"] == "counting" and d["campaign"]["views_until"] == NOW + 2 * DAY


def test_the_dashboard_shows_the_grace_period_and_held_platforms():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "counting: 'Counting views'" in page
    pg = page[page.index("function CampaignPage("):page.index("function CampaignsScreen(")]
    assert "Views count until" in pg and "Posting closes" in pg
    assert "const held = c.held || [];" in pg and "postable.length > 0" in pg
    assert "c.status === 'counting'" in pg
    when = page[page.index("function campWhen("):][:700]
    assert "c.status === 'counting'" in when and "c.views_until - now" in when
    card = page[page.index("function PromoteCard("):][:3000]
    assert "now < (c.views_until || 0) ? 'counting'" in card and "counting: ['Counting views', 'ok']" in card
    # A counting campaign is still in the default view, not filed under Ended.
    grid = page[page.index("function CampaignsScreen("):][:2400]
    assert "open: rows.filter(c => c.status !== 'ended')" in grid


def test_the_admin_page_sets_it_and_waits_for_it():
    from src.dashboard import api
    html = api.ADMIN_HTML
    assert 'name="views_until"' in html and 'id="cp-grace"' in html
    assert "views_until: epoch(v('views_until'))" in html
    results = html[html.index("function renderResults("):][:1800]
    assert "d.status === 'counting'" in results
    assert ".cp-pill.counting" in html


def test_the_blog_says_some_campaigns_keep_counting(monkeypatch):
    from src.dashboard import api
    from src.dashboard import blog_content as B
    monkeypatch.setattr(api.settings, "campaigns_enabled", True)
    text = " ".join(str(b[1]) for b in B.campaigns_article().blocks if b[0] == "p")
    assert "keep counting views for a few days after posting closes" in text
