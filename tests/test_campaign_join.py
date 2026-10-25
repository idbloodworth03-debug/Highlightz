"""Join a campaign to see its full details (owner, 2026-10-25: "make it so
people click "join" in the campaign page and then they can see the full
details").

What these defend:
  1. the join store: once per person, kept across a restart, gone with the
     account or the campaign;
  2. joining: anyone who can see the marketplace, while the campaign is
     upcoming or live, told to that account's other tabs only;
  3. until someone joins, the rules, the tags and caption a post needs and the
     leaderboard are not sent at all (not merely hidden by the page); after it
     they are; admins see what clippers see; anyone with an entry counts as
     joined; once posting has closed the page is open to everyone;
  4. the admin list counts who joined;
  5. the dashboard, the Post dialog and the copy follow it.
"""

import base64
import json
import time

import pytest
from itsdangerous import TimestampSigner

from src import campaigns as camp
from src.auth import users as _user_store
from src.campaigns import joins
from src.campaigns import requests as creq
from src.campaigns import results as res
from src.publish import schedule as sched

NOW = time.time()
REAL_ADMIN_IDS = _user_store.admin_ids            # before any fixture swaps it
DAY = 86400
SECRET = "Only clips of the Friday stream count."
PEOPLE = {
    "boss": {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
    "ann": {"id": "ann", "username": "ann", "twitch_login": "ann_clips", "subscription_status": "active"},
    "bob": {"id": "bob", "username": "bob", "subscription_status": "active"},
}


def form(**kw):
    base = {"title": "Duxxion's Campaign", "summary": "Clip duxxion's streams.", "rules": SECRET,
            "start_at": NOW - DAY, "end_at": NOW + 6 * DAY, "views_until": NOW + 9 * DAY,
            "streamers": [{"name": "duxxion", "platform": "twitch"}], "prize_pool": 500,
            "payouts": [250, 150, 100], "judging": "top_clip_views", "platforms": ["tiktok", "instagram"],
            "hashtags": "#duxxion", "mentions": "@duxxion", "caption_template": "{title} | clipped from {streamer}",
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
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": PEOPLE[uid].get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c
    c.login, c.sent, c.api = login, sent, api
    return c


def row(client, who, cid):
    return next(r for r in client.login(who).get("/campaigns").json()["rows"] if r["id"] == cid)


# ── 1. the store ─────────────────────────────────────────────────────────────

def test_joining_is_once_per_person_and_survives_a_restart():
    assert joins.join("c1", "ann") is True and joins.join("c1", "ann") is False
    joins.join("c1", "bob")
    joins.join("c2", "ann")
    joins._joins.clear(); joins._loaded = False            # a restart
    assert joins.has("c1", "ann") and joins.count("c1") == 2 and not joins.has("c3", "ann")
    joins.forget_user("ann")
    assert not joins.has("c1", "ann") and not joins.has("c2", "ann") and joins.count("c1") == 1
    joins.drop("c1")
    assert joins.count("c1") == 0


# ── 2. joining ───────────────────────────────────────────────────────────────

def test_joining_opens_it_for_that_account_and_tells_only_its_tabs(client):
    c = camp.create(form())
    r = client.login("ann").post(f"/campaigns/{c['id']}/join")
    assert r.status_code == 200 and joins.has(c["id"], "ann")
    assert client.sent == [("campaigns_changed", "ann")], "only ann's tabs need to re-read"
    client.sent.clear()
    assert client.login("ann").post(f"/campaigns/{c['id']}/join").status_code == 200
    assert client.sent == [], "joining twice changes nothing"


def test_joining_is_open_while_upcoming_or_live_only(client):
    soon = camp.create(form(start_at=NOW + DAY, end_at=NOW + 8 * DAY, views_until=0))
    counting = camp.create(form(start_at=NOW - 8 * DAY, end_at=NOW - DAY, views_until=NOW + 2 * DAY))
    ended = camp.create(form(start_at=NOW - 8 * DAY, end_at=NOW - DAY, views_until=0))
    assert client.login("ann").post(f"/campaigns/{soon['id']}/join").status_code == 200
    for c in (counting, ended):
        r = client.login("ann").post(f"/campaigns/{c['id']}/join")
        assert r.status_code == 400 and "closed" in r.json()["detail"]


def test_only_those_who_can_see_it_can_join(client, monkeypatch):
    draft = camp.create(form(published=False))
    assert client.login("ann").post(f"/campaigns/{draft['id']}/join").status_code == 404
    live = camp.create(form())
    monkeypatch.setattr(client.api.settings, "campaigns_enabled", False)
    assert client.login("ann").post(f"/campaigns/{live['id']}/join").status_code == 404
    assert client.login("boss").post(f"/campaigns/{draft['id']}/join").status_code == 200


# ── 3. what is sent before and after ─────────────────────────────────────────

def test_the_details_are_not_sent_until_they_join(client):
    c = camp.create(form())
    r = client.login("ann").get("/campaigns")
    before = next(x for x in r.json()["rows"] if x["id"] == c["id"])
    assert before["locked"] is True and before["joined"] is False
    assert (before["rules"], before["hashtags"], before["mentions"], before["caption_template"], before["board"]) == \
        ("", [], [], "", None)
    assert SECRET not in r.text and "clipped from {streamer}" not in r.text, "the details leaked"
    # The overview is all there.
    for k, v in (("title", "Duxxion's Campaign"), ("prize_pool", 500), ("payouts", [250, 150, 100]),
                 ("judging_text", "Most views on a single clip"), ("platforms", ["tiktok", "instagram"])):
        assert before[k] == v, k
    assert before["streamers"][0]["name"] == "duxxion" and before["views_until"] == NOW + 9 * DAY
    single = client.login("ann").get(f"/campaigns/{c['id']}")
    assert single.status_code == 200 and single.json()["locked"] and SECRET not in single.text
    client.login("ann").post(f"/campaigns/{c['id']}/join")
    after = row(client, "ann", c["id"])
    assert after["locked"] is False and after["joined"] is True
    assert after["rules"] == SECRET and after["hashtags"] == ["duxxion"] and after["mentions"] == ["duxxion"]
    assert after["caption_template"] and isinstance(after["board"], dict)
    assert row(client, "bob", c["id"])["locked"] is True, "ann joining opened it for bob"


def test_an_admin_sees_what_clippers_see(client):
    c = camp.create(form())
    assert row(client, "boss", c["id"])["locked"] is True
    client.login("boss").post(f"/campaigns/{c['id']}/join")
    assert row(client, "boss", c["id"])["rules"] == SECRET


def test_someone_with_an_entry_counts_as_joined(client):
    c = camp.create(form())
    it = sched.Item(id="it0001", user_id="bob", upload_id="u", filename="f.mp4", caption="#duxxion @duxxion",
                    platforms=["tiktok"], status="posted", campaign_id=c["id"])
    it.results = {"tiktok": {"status": "posted", "url": "https://www.tiktok.com/@bob/video/1", "at": NOW - 3600}}
    sched._items[it.id] = it
    sched._save()
    r = row(client, "bob", c["id"])
    assert r["joined"] is True and r["locked"] is False and r["board"]["you"]["rank"] == 1
    assert not joins.has(c["id"], "bob"), "an entry counts as joined without writing a join"


def test_once_posting_closes_the_page_is_open_to_everyone(client):
    counting = camp.create(form(start_at=NOW - 8 * DAY, end_at=NOW - DAY, views_until=NOW + 2 * DAY))
    ended = camp.create(form(start_at=NOW - 8 * DAY, end_at=NOW - DAY, views_until=0))
    for c in (counting, ended):
        r = row(client, "ann", c["id"])
        assert r["locked"] is False and r["joined"] is False and r["rules"] == SECRET and r["board"] is not None


def test_a_deleted_campaign_takes_its_joins(client):
    c = camp.create(form())
    client.login("ann").post(f"/campaigns/{c['id']}/join")
    assert client.login("boss").delete(f"/admin/campaigns/{c['id']}").status_code == 200
    assert joins.count(c["id"]) == 0


def test_a_deleted_account_takes_its_joins():
    import inspect
    from src.dashboard import api
    assert "_joins_purge.forget_user(uid)" in inspect.getsource(api.delete_account)
    assert "_joins_purge.forget_user(user_id)" in inspect.getsource(api.admin_delete_user)


# ── 4. how many joined: the admin list, and every clipper ────────────────────

def test_everyone_sees_how_many_joined(client):
    c = camp.create(form())
    for who in ("ann", "boss"):
        client.login(who).post(f"/campaigns/{c['id']}/join")
    # bob posted before joining existed: he counts once, as he counts as joined.
    it = sched.Item(id="it0001", user_id="bob", upload_id="u", filename="f.mp4", caption="#duxxion @duxxion",
                    platforms=["tiktok"], status="posted", campaign_id=c["id"])
    it.results = {"tiktok": {"status": "posted", "url": "https://www.tiktok.com/@bob/video/1", "at": NOW - 3600}}
    sched._items[it.id] = it
    sched._save()
    # ann and bob; boss joined too, but admins are not counted.
    for who in ("ann", "bob", "boss"):
        assert row(client, who, c["id"])["joined_count"] == 2, who
    joins.join(c["id"], "bob")                    # pressing Join after posting does not count twice
    assert row(client, "ann", c["id"])["joined_count"] == 2
    assert client.login("ann").get(f"/campaigns/{c['id']}").json()["joined_count"] == 2


def test_an_admin_still_has_to_join_but_is_not_counted(client, monkeypatch):
    c = camp.create(form())
    due = []
    monkeypatch.setattr(client.api, "_send_join_count_soon", lambda cid: due.append(cid))
    assert row(client, "boss", c["id"])["locked"] is True, "admins see the overview until they join"
    client.login("boss").post(f"/campaigns/{c['id']}/join")
    mine = row(client, "boss", c["id"])
    assert mine["locked"] is False and mine["rules"] == SECRET, "joining still opens it for an admin"
    assert mine["joined_count"] == 0 and row(client, "ann", c["id"])["joined_count"] == 0
    admin_row = next(r for r in client.login("boss").get("/admin/campaigns").json()["rows"] if r["id"] == c["id"])
    assert admin_row["joined_count"] == 0
    assert due == [], "the count did not change, so nobody is sent one"
    client.login("ann").post(f"/campaigns/{c['id']}/join")
    assert row(client, "bob", c["id"])["joined_count"] == 1 and due == [c["id"]]


def test_making_someone_admin_or_not_moves_everyone_s_count(client, monkeypatch):
    from src.auth import users as user_store

    def _set_admin(uid, on):
        monkeypatch.setitem(PEOPLE, uid, {**PEOPLE[uid], "is_admin": on})
        return True
    monkeypatch.setattr(user_store, "set_admin", _set_admin)
    c = camp.create(form())
    client.login("ann").post(f"/campaigns/{c['id']}/join")
    assert row(client, "bob", c["id"])["joined_count"] == 1
    client.sent.clear()
    assert client.login("boss").post("/admin/users/ann/admin?on=true").status_code == 200
    assert ("campaigns_changed", None) in client.sent, "everyone's count is out of date"
    assert row(client, "bob", c["id"])["joined_count"] == 0
    client.sent.clear()
    assert client.login("boss").post("/admin/users/bob/admin?on=true").status_code == 200
    assert ("campaigns_changed", None) not in client.sent, "bob joined nothing: no count moved"


def test_the_users_file_says_who_the_admins_are(tmp_path, monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    user_store._save([{"id": "a", "username": "ann", "is_admin": True}, {"id": "b", "username": "bob"}])
    assert REAL_ADMIN_IDS() == frozenset({"a"})
    user_store._save([{"id": "a", "username": "ann"}, {"id": "b", "username": "bob", "is_admin": True}])
    assert REAL_ADMIN_IDS() == frozenset({"b"}), "a changed file is a new answer"


def test_a_join_tells_everyone_the_new_count_once_per_burst(monkeypatch):
    import asyncio
    from src.dashboard import api
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg, user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api, "_JOIN_COUNT_EVERY", 0.05)
    monkeypatch.setattr(api, "_join_count_due", set())
    c = camp.create(form())
    draft = camp.create(form(published=False))

    async def run():
        for who in ("ann", "bob", "boss"):        # a burst: three joins at once
            joins.join(c["id"], who)
            api._send_join_count_soon(c["id"])
        joins.join(draft["id"], "boss")
        api._send_join_count_soon(draft["id"])
        await asyncio.sleep(0.2)
        # ann and bob (boss is an admin, not counted).
        assert sent == [({"event": "campaign_join_count", "campaign_id": c["id"], "joined_count": 2}, None)], \
            "one event for the burst, with the latest count, to everyone; nothing for the draft"
        sent.clear()
        joins.forget_user("bob")
        api._send_join_count_soon(c["id"])        # the next one goes out on its own
        await asyncio.sleep(0.2)
        assert [m["joined_count"] for m, _u in sent] == [1]
    asyncio.run(run())


def test_the_join_endpoint_sends_the_count_for_a_new_join_only(client, monkeypatch):
    c = camp.create(form())
    due = []
    monkeypatch.setattr(client.api, "_send_join_count_soon", lambda cid: due.append(cid))
    client.login("ann").post(f"/campaigns/{c['id']}/join")
    client.login("ann").post(f"/campaigns/{c['id']}/join")
    assert due == [c["id"]]


def test_a_deleted_account_lowers_everyone_s_counts():
    import inspect
    from src.dashboard import api
    assert "or left:" in inspect.getsource(api.delete_account)
    assert "or left" in inspect.getsource(api.admin_delete_user)
    assert joins.join("c1", "ann") and joins.forget_user("ann") is True and joins.forget_user("ann") is False


def test_the_admin_list_counts_who_joined(client):
    c = camp.create(form())
    for who in ("ann", "bob"):
        client.login(who).post(f"/campaigns/{c['id']}/join")
    rows = client.login("boss").get("/admin/campaigns").json()["rows"]
    assert next(r for r in rows if r["id"] == c["id"])["joined_count"] == 2
    assert "(c.joined_count || 0) + ' joined'" in client.api.ADMIN_HTML


def test_admins_see_who_is_in_a_campaign(client, monkeypatch):
    """Owner: "make it so admins can see who is in the campaign in the admin
    portal"."""
    monkeypatch.setitem(PEOPLE, "ann", {**PEOPLE["ann"], "plan": "pro"})
    monkeypatch.setitem(PEOPLE, "cat", {"id": "cat", "username": "cat", "plan": "free",
                                        "subscription_status": "none"})
    monkeypatch.setattr(client.api.settings, "uploads_enabled", True)
    c = camp.create(form())
    for who, at in (("ann", NOW - 300), ("boss", NOW - 200), ("cat", NOW - 100)):
        joins.join(c["id"], who)
        joins._joins[c["id"]][who] = at
    # bob posted before joining existed; one of his two entries counts.
    for i, at in enumerate((NOW - 3600, NOW + 10 * DAY)):
        it = sched.Item(id=f"it000{i}", user_id="bob", upload_id="u", filename="f.mp4", caption="#duxxion",
                        platforms=["tiktok"], status="posted", campaign_id=c["id"])
        it.results = {"tiktok": {"status": "posted", "url": f"https://www.tiktok.com/@bob/video/{i}", "at": at}}
        sched._items[it.id] = it
    sched._save()
    assert client.login("ann").get(f"/admin/campaigns/{c['id']}/members").status_code == 403, "admins only"
    d = client.login("boss").get(f"/admin/campaigns/{c['id']}/members").json()
    assert (d["clippers"], d["admins"]) == (3, 1)
    got = [(r["name"], r["admin"], r["plan"], r["can_post"], r["entries"], r["counting"], bool(r["joined_at"]))
           for r in d["rows"]]
    assert got == [("cat", False, "free", False, 0, 0, True),          # newest first
                   ("boss", True, "pro", True, 0, 0, True),
                   ("ann_clips", False, "pro", True, 0, 0, True),
                   ("bob", False, got[3][2], got[3][3], 2, 1, False)], got   # joined by posting
    # Nothing private: no emails, payout details or passwords in the list.
    assert "email" not in json.dumps(d) and "password" not in json.dumps(d)


def test_the_admin_page_has_who_joined():
    from src.dashboard import api
    html = api.ADMIN_HTML
    assert 'id="cp-members"' in html and "cp-mem" in html and ">Who joined</button>" in html
    assert "'/admin/campaigns/' + id + '/members'" in html
    poll = html[html.index("if(!campaignsOpen()) return;"):][:200]
    assert "cpMembers(CM_ID, true)" in poll, "the open list is not re-read with the page"
    assert "admin, not counted" in html and "cannot post entries" in html


# ── 5. the pages ─────────────────────────────────────────────────────────────

def test_the_dashboard_follows_it():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    join = page[page.index("function CampaignJoin("):page.index("function CampaignPage(")]
    assert "fetch('/campaigns/' + c.id + '/join', {method: 'POST'})" in join
    assert "onRefresh();" in join and "Join campaign" in join
    pg = page[page.index("function CampaignPage("):page.index("function CampaignsScreen(")]
    assert "const locked = !!c.locked;" in pg and "{locked && <CampaignJoin" in pg
    assert "{!locked && <section className=\"cmp-sec\">" in pg, "the rules section is not held back"
    assert "postable.length > 0 && !locked" in pg
    # Posting for a campaign is only offered for joined ones.
    assert "campaigns={(campaigns||[]).filter(c=>c.status==='live' && !c.locked)}" in page
    assert "c.status==='live' && !c.locked);" in page
    card = page[page.index("function CampaignCard("):][:2400]
    assert "c.joined &&" in card and "You joined" in card
    # The joined count: on the card, the page and the Join panel, and patched
    # in place by its own event.
    assert "campJoinedCount(c.joined_count)" in card and "campJoinedCount(c.joined_count)" in pg
    assert "campJoinedCount(c.joined_count) + ' so far.'" in join and "Be the first to join." in join
    at = page.index("msg.event==='campaign_join_count'")
    ws = page[at:page.index("else if(msg.event===", at)]
    assert "canCampaignsRef.current" in ws and "joined_count: msg.joined_count" in ws and "refetch" not in ws


def test_after_joining_the_page_shows_a_live_counter():
    """Owner: "put a counter that tells people the current amount of people
    that joined inside the page after they hit join"."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    box = page[page.index("function CampaignJoinedCount("):page.index("function CampaignPage(")]
    assert "const n = c.joined_count || 0;" in box and "campNum(n)" in box
    assert "key={n}" in box, "the number does not bump when it changes"
    assert 'aria-live="polite"' in box and "Clippers joined" in box and "Clipper joined" in box
    pg = page[page.index("function CampaignPage("):page.index("function CampaignsScreen(")]
    assert "const counter = !locked && c.joined && joinable;" in pg and "{counter && <CampaignJoinedCount c={c}/>}" in pg
    # One place for the count at a time: the panels while it can be joined,
    # the header once posting has closed.
    assert "{!locked && !counter && c.joined_count > 0 &&" in pg
    # Live: the number is the listing's, which campaign_join_count patches.
    at = page.index("msg.event==='campaign_join_count'")
    assert "joined_count: msg.joined_count" in page[at:page.index("else if(msg.event===", at)]
    css = page[page.index(".cmp-count{"):page.index(".cmp-count{") + 900]
    assert "@media(prefers-reduced-motion:reduce){.cmp-count-n{animation:none}}" in css
    assert "animation:cmp-bump var(--dur-slow) var(--ease)" in css


def test_the_copy_says_to_join(monkeypatch):
    from src.dashboard import api
    from src.dashboard import blog_content as B
    monkeypatch.setattr(api.settings, "campaigns_enabled", True)
    text = " ".join(str(b[1]) for b in B.campaigns_article().blocks if b[0] == "p")
    assert "Clippers join the campaign" in text
    import inspect
    assert "Join a campaign, clip the streams it names" in inspect.getsource(api._campaigns_block)
