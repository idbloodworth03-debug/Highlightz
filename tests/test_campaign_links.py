"""Entry links (owner, 2026-10-25: "let them post their entry links if they
want. Lets do that so it makes it easier to grade and score people and find
the winners ... But dont make it forced").

What these defend:
  1. a link is read for its platform and one key per post, however it was
     copied; anything that is not a post on TikTok, Instagram or YouTube is
     refused with a reason;
  2. a link entry is an entry like any other: counted by an admin, scored by
     the campaign's rule, on the same leaderboard; only on a platform the
     campaign names, never the same post twice, at most LINK_MAX per clipper,
     none once winners stand; a clipper takes back only their own links;
  3. the API: any account that joined (free ones included) while the
     campaign is live; their tabs hear it at once, everyone's leaderboard a
     moment later, once for a burst;
  4. privacy: a clipper sees their own entries and links, nobody else's;
  5. optional: posting from Highlightz is untouched, and the pages offer both.
"""

import asyncio
import base64
import json
import time

import pytest
from itsdangerous import TimestampSigner

from src import campaigns as camp
from src.campaigns import joins, links
from src.campaigns import requests as creq
from src.campaigns import results as res
from src.publish import schedule as sched

NOW = time.time()
DAY = 86400
PEOPLE = {
    "boss": {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
    "ann": {"id": "ann", "username": "ann", "twitch_login": "ann_clips", "plan": "pro", "subscription_status": "active"},
    "fred": {"id": "fred", "username": "fred", "twitch_login": "fredclips", "plan": "free", "subscription_status": "none"},
}
TT = "https://www.tiktok.com/@fredclips/video/7301234567890123456"
IG = "https://www.instagram.com/reel/C8xYz12AbCd/"


def form(**kw):
    base = {"title": "Duxxion's Campaign", "summary": "s", "rules": "r", "start_at": NOW - DAY, "end_at": NOW + 6 * DAY,
            "streamers": [{"name": "duxxion", "platform": "twitch"}], "prize_pool": 500, "payouts": [250, 150, 100],
            "judging": "top_clip_views", "platforms": ["tiktok", "instagram"], "hashtags": "#duxxion",
            "published": True}
    base.update(kw)
    return base


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


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.auth import users as user_store
    from src.dashboard import api
    monkeypatch.setattr(user_store, "_load", lambda: [dict(u) for u in PEOPLE.values()])
    monkeypatch.setattr(user_store, "_save", lambda u: None)
    monkeypatch.setattr(api.settings, "campaigns_enabled", True)
    sent, soon = [], []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api, "_campaigns_changed_soon", lambda: soon.append(1))
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": PEOPLE[uid].get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c
    c.login, c.sent, c.soon, c.api = login, sent, soon, api
    return c


def board(client, who, cid):
    return next(r for r in client.login(who).get("/campaigns").json()["rows"] if r["id"] == cid)["board"]


# ── 1. reading a link ────────────────────────────────────────────────────────

@pytest.mark.parametrize("url,platform,key", [
    (TT + "?is_from_webapp=1&sender_device=pc", "tiktok", "tiktok:7301234567890123456"),
    ("m.tiktok.com/@fredclips/video/7301234567890123456/", "tiktok", "tiktok:7301234567890123456"),
    ("https://vm.tiktok.com/ZMabc123/", "tiktok", "tiktok:vm/ZMabc123"),
    ("https://www.tiktok.com/t/ZTabc123/", "tiktok", "tiktok:t/ZTabc123"),
    (IG + "?igsh=abc123", "instagram", "instagram:C8xYz12AbCd"),
    ("instagram.com/reels/C8xYz12AbCd", "instagram", "instagram:C8xYz12AbCd"),
    ("https://www.instagram.com/fredclips/reel/C8xYz12AbCd/", "instagram", "instagram:C8xYz12AbCd"),
    ("https://www.instagram.com/p/C9pPpPpPpPp/", "instagram", "instagram:C9pPpPpPpPp"),
    ("https://youtube.com/shorts/abcDEF12345?feature=share", "youtube", "youtube:abcDEF12345"),
    ("https://www.youtube.com/watch?v=abcDEF12345&t=3", "youtube", "youtube:abcDEF12345"),
    ("https://youtu.be/abcDEF12345", "youtube", "youtube:abcDEF12345"),
])
def test_a_link_is_one_post_however_it_was_copied(url, platform, key):
    got_platform, got_key, clean = links.parse(url)
    assert (got_platform, got_key) == (platform, key)
    assert "?" not in clean or "watch?v=" in clean, "tracking left on the link"
    assert links.parse(clean)[1] == key, "the clean link is the same post"


@pytest.mark.parametrize("url", [
    "", "not a link", "https://evil.example/@fredclips/video/7301234567890123456",
    "javascript:alert(1)", "https://www.tiktok.com/@fredclips", "ftp://tiktok.com/@a/video/7301234567",
    "https://www.instagram.com/fredclips/", "https://www.youtube.com/@channel",
    "https://www.tiktok.com/@a/video/1234567 extra", "https://www.tiktok.com/@a/video/" + "1" * 500,
])
def test_anything_that_is_not_a_post_link_is_refused(url):
    with pytest.raises(links.LinkError):
        links.parse(url)
    assert links.key_of(url) == ""


# ── 2. the rules ─────────────────────────────────────────────────────────────

def test_a_link_entry_counts_like_any_other():
    c = camp.create(form())
    e = res.add_link_entry(c, "fred", TT + "?x=1")
    assert e["key"].startswith("link:") and e["url"] == TT and e["platform"] == "tiktok"
    ent = res.entries(c)[0]
    assert ent["source"] == "link" and ent["eligible"] and not ent["counted"]
    res.save_stats(c, [{"key": e["key"], "views": 12000}], "boss")
    assert [(r["name"], r["score"]) for r in res.standings(c)] == [("fredclips", 12000)]


def test_only_platforms_the_campaign_names(monkeypatch):
    c = camp.create(form(platforms=["tiktok"]))
    with pytest.raises(res.ResultsError, match="Instagram posts do not count for this campaign. It counts TikTok"):
        res.add_link_entry(c, "fred", IG)


def test_one_post_is_one_entry():
    c = camp.create(form())
    res.add_link_entry(c, "fred", TT)
    for again in (TT + "?share=1", "m.tiktok.com/@fredclips/video/7301234567890123456"):
        with pytest.raises(res.ResultsError, match="already an entry"):
            res.add_link_entry(c, "ann", again)
    # Nor a post already entered from Highlightz.
    it = sched.Item(id="it0001", user_id="ann", upload_id="u", filename="f.mp4", caption="#duxxion",
                    platforms=["tiktok"], status="posted", campaign_id=c["id"])
    it.results = {"tiktok": {"status": "posted", "url": "https://www.tiktok.com/@ann_clips/video/7309999999999999999",
                             "at": NOW - 3600}}
    sched._items[it.id] = it
    sched._save()
    with pytest.raises(res.ResultsError, match="already an entry"):
        res.add_link_entry(c, "fred", "https://www.tiktok.com/@ann_clips/video/7309999999999999999")


def test_a_cap_per_clipper(monkeypatch):
    monkeypatch.setattr(res, "LINK_MAX", 2)
    c = camp.create(form(platforms=["youtube"]))
    res.add_link_entry(c, "fred", "https://youtu.be/aaaaaaaaaaa1")
    res.add_link_entry(c, "fred", "https://youtu.be/aaaaaaaaaaa2")
    with pytest.raises(res.ResultsError, match="up to 2 links"):
        res.add_link_entry(c, "fred", "https://youtu.be/aaaaaaaaaaa3")
    res.add_link_entry(c, "ann", "https://youtu.be/aaaaaaaaaaa3")        # someone else's own count


def test_taking_back_your_own_link_only():
    c = camp.create(form())
    mine = res.add_link_entry(c, "fred", TT)["key"]
    theirs = res.add_link_entry(c, "ann", IG)["key"]
    res.save_stats(c, [{"key": mine, "views": 5}], "boss")
    with pytest.raises(res.ResultsError, match="not one of your entry links"):
        res.remove_link_entry(c, "fred", theirs)
    res.remove_link_entry(c, "fred", mine)
    assert [e["key"] for e in res.entries(c)] == [theirs]
    assert mine not in res._peek(c["id"])["stats"], "its count stayed behind"


def test_nothing_changes_once_winners_stand():
    c = camp.create(form(start_at=NOW - 8 * DAY, end_at=NOW + DAY))
    k = res.add_link_entry(c, "fred", TT)["key"]
    res.save_stats(c, [{"key": k, "views": 9}], "boss")
    c = camp.update(c["id"], form(start_at=NOW - 8 * DAY, end_at=NOW - 60))
    res.confirm(c, res.propose(c), "boss")
    with pytest.raises(res.ResultsError, match="winners are confirmed"):
        res.add_link_entry(c, "ann", IG)
    with pytest.raises(res.ResultsError, match="winners are confirmed"):
        res.remove_link_entry(c, "fred", k)


# ── 3. the API ───────────────────────────────────────────────────────────────

def test_any_account_that_joined_can_add_one_while_it_is_live(client):
    c = camp.create(form())
    r = client.login("fred").post(f"/campaigns/{c['id']}/entries", json={"url": TT})
    assert r.status_code == 400 and "Join this campaign first" in r.json()["detail"]
    client.login("fred").post(f"/campaigns/{c['id']}/join")
    client.sent.clear()
    r = client.login("fred").post(f"/campaigns/{c['id']}/entries", json={"url": TT + "?x=1"})
    assert r.status_code == 200, r.text
    assert r.json()["url"] == TT and r.json()["platform"] == "tiktok"
    assert client.sent == [("campaigns_changed", "fred")] and client.soon == [1], \
        "their tabs at once, everyone's leaderboard a moment later"
    bad = client.login("fred").post(f"/campaigns/{c['id']}/entries", json={"url": "https://example.com/x"})
    assert bad.status_code == 400 and "TikTok, Instagram or YouTube" in bad.json()["detail"]


def test_not_before_it_starts_or_after_posting_closes(client):
    soon = camp.create(form(start_at=NOW + DAY, end_at=NOW + 8 * DAY))
    closed = camp.create(form(start_at=NOW - 8 * DAY, end_at=NOW - DAY, views_until=NOW + 2 * DAY))
    for c in (soon, closed):
        joins.join(c["id"], "fred")
    assert "not started" in client.login("fred").post(f"/campaigns/{soon['id']}/entries", json={"url": TT}).json()["detail"]
    assert "closed" in client.login("fred").post(f"/campaigns/{closed['id']}/entries", json={"url": TT}).json()["detail"]


def test_their_entries_are_theirs_to_see_and_nobody_elses(client):
    c = camp.create(form())
    for who in ("fred", "ann"):
        joins.join(c["id"], who)
    client.login("fred").post(f"/campaigns/{c['id']}/entries", json={"url": TT})
    client.login("ann").post(f"/campaigns/{c['id']}/entries", json={"url": IG})
    fb = board(client, "fred", c["id"])
    assert [(e["url"], e["source"], e["removable"]) for e in fb["mine"]] == [(TT, "link", True)]
    assert IG not in json.dumps(fb) and "C8xYz12AbCd" not in json.dumps(fb), "ann's link reached fred"
    assert [r["name"] for r in fb["rows"]] == ["ann_clips", "fredclips"] or len(fb["rows"]) == 2
    # Taking it back.
    key = fb["mine"][0]["key"]
    assert client.login("ann").delete(f"/campaigns/{c['id']}/entries/{key}").status_code == 400
    assert client.login("fred").delete(f"/campaigns/{c['id']}/entries/{key}").status_code == 200
    assert board(client, "fred", c["id"])["mine"] == []


def test_admins_see_link_entries_in_results(client):
    c = camp.create(form())
    joins.join(c["id"], "fred")
    client.login("fred").post(f"/campaigns/{c['id']}/entries", json={"url": TT})
    view = client.login("boss").get(f"/admin/campaigns/{c['id']}/results").json()
    assert [(e["source"], e["url"], e["name"]) for e in view["entries"]] == [("link", TT, "fredclips")]


def test_a_burst_of_links_is_one_leaderboard_update(monkeypatch):
    from src.dashboard import api
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api, "_CHANGED_EVERY", 0.05)
    monkeypatch.setattr(api, "_changed_due", False)

    async def run():
        for _ in range(5):
            api._campaigns_changed_soon()
        await asyncio.sleep(0.2)
        assert sent == [("campaigns_changed", None)]
        api._campaigns_changed_soon()
        await asyncio.sleep(0.2)
        assert len(sent) == 2
    asyncio.run(run())


# ── 5. optional, and the pages ───────────────────────────────────────────────

def test_the_page_offers_links_without_forcing_them():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    mine = page[page.index("function CampaignMyEntries("):page.index("function CampaignPage(")]
    assert "send('POST', '/campaigns/' + c.id + '/entries', {url: url.trim()})" in mine
    assert "send('DELETE', '/campaigns/' + c.id + '/entries/' + encodeURIComponent(key))" in mine
    assert "{live && <form className=\"cmp-add\"" in mine and "Optional." in mine
    assert "e.removable &&" in mine and "Posted from Highlightz" in mine and "Added by link" in mine
    pg = page[page.index("function CampaignPage("):page.index("function CampaignsScreen(")]
    assert "{!locked && c.joined && <CampaignMyEntries" in pg
    # Posting from Highlightz is still there for Pro.
    assert "Post a clip for this campaign" in pg
    # No backslashes were needed in the new JS (the page is a Python string).
    assert "campShortUrl = u => String(u || '').replace(/^https?:[/][/](www[.])?/, '')" in page


def test_the_admin_results_mark_link_entries():
    from src.dashboard import api
    html = api.ADMIN_HTML
    assert "e.source === 'link'" in html and ">link</span>" in html
    assert "were posted outside Highlightz and added by their link" in html
