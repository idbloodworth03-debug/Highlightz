"""The Campaigns marketplace (owner, 2026-09-30).

"I basically want it showcase all of these things and have a whole marketplace"
— campaigns with dates, rules, streamers, prize pool and split, judging — created
"inside the admin page … add a picture and insert all the rules", and "connected
to posting … so you can post straight to instagram or tiktok and already have it
using the correct rules (like hashtags needed or certain caption)".

What these defend:
  1. the form is validated (dates in order, payouts not over the pool, a
     platform, clean hashtags/handles, a note for custom judging);
  2. only admins write; the marketplace is admin-only until CAMPAIGNS_ENABLED,
     then published campaigns only (drafts stay admin-only);
  3. the picture is sniffed from its bytes and size-capped;
  4. a campaign post is refused unless the campaign is live, the platform
     counts, and every required hashtag/mention is in the caption — then the
     post carries the campaign and shows in its entries;
  5. every admin change broadcasts campaigns_changed, and the page handles it.
"""

import base64
import json
import time

import pytest
from itsdangerous import TimestampSigner

from src import campaigns as camp
from src.publish import connections, poster, schedule as sched
from src.uploads import library as lib

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64
NOW = time.time()


def form(**kw):
    base = {"title": "Clipping Cup #1", "summary": "Clip three streamers, win $500.",
            "rules": "1. Be nice.\n2. Clips must be yours.", "start_at": NOW - 3600,
            "end_at": NOW + 7 * 86400, "streamers": [{"name": "jynxzi", "platform": "twitch"},
                                                     {"name": "gymskin", "platform": "kick"}],
            "prize_pool": 500, "payouts": [250, 150, 100], "judging": "best_n_engagement",
            "judging_n": 3, "platforms": ["tiktok", "instagram"], "hashtags": "#HighlightzCup #clips",
            "mentions": "@highlightz", "caption_template": "{title} | from {streamer}", "published": True}
    base.update(kw)
    return base


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(camp, "_INDEX", tmp_path / "campaigns.json")
    monkeypatch.setattr(camp, "_IMAGES", tmp_path / "campaign_images")
    camp._campaigns.clear(); camp._loaded = False
    yield
    camp._campaigns.clear(); camp._loaded = False


# ── validation ───────────────────────────────────────────────────────────────

def test_a_full_campaign_is_stored_cleanly():
    c = camp.create(form())
    assert c["hashtags"] == ["HighlightzCup", "clips"] and c["mentions"] == ["highlightz"]
    assert c["streamers"][1] == {"name": "gymskin", "platform": "kick", "url": "https://kick.com/gymskin"}
    p = camp.public(c)
    assert p["status"] == "live" and p["winners"] == 3
    assert p["judging_text"] == "Most engagement (views + likes + comments) across your best 3 clips"
    camp._campaigns.clear(); camp._loaded = False           # survives a restart
    assert camp.get(c["id"])["title"] == "Clipping Cup #1"


@pytest.mark.parametrize("bad,msg", [
    ({"title": ""}, "title"),
    ({"end_at": NOW - 7200}, "after the start"),
    ({"payouts": [400, 200]}, "more than"),
    ({"platforms": []}, "platform"),
    ({"hashtags": "#bad-tag"}, "not a valid hashtag"),
    ({"judging": "custom", "judging_note": ""}, "Describe how"),
    ({"streamers": [{"name": "no spaces allowed", "platform": "twitch"}]}, "channel name"),
])
def test_the_form_is_checked(bad, msg):
    with pytest.raises(camp.CampaignError) as e:
        camp.create(form(**bad))
    assert msg in str(e.value)


def test_status_follows_the_clock():
    c = camp.create(form(start_at=NOW + 86400, end_at=NOW + 2 * 86400))
    assert camp.status_of(c, NOW) == "upcoming"
    assert camp.status_of(c, NOW + 1.5 * 86400) == "live"
    assert camp.status_of(c, NOW + 3 * 86400) == "ended"


def test_the_listing_puts_live_first_then_upcoming_then_ended():
    ended = camp.create(form(title="Old", start_at=NOW - 9 * 86400, end_at=NOW - 86400))
    up = camp.create(form(title="Soon", start_at=NOW + 86400, end_at=NOW + 3 * 86400))
    live = camp.create(form(title="Now"))
    assert [r["title"] for r in camp.listing(include_drafts=True)] == ["Now", "Soon", "Old"]


def test_the_picture_is_sniffed_and_capped():
    c = camp.create(form())
    with pytest.raises(camp.CampaignError):
        camp.set_image(c["id"], b"<svg onload=alert(1)>")
    with pytest.raises(camp.CampaignError):
        camp.set_image(c["id"], PNG + b"\x00" * camp.IMAGE_MAX)
    c = camp.set_image(c["id"], PNG)
    assert c["image"].endswith(".png") and camp.image_path(c["id"]).read_bytes() == PNG


def test_caption_rules_match_whole_tags_only():
    c = camp.create(form())
    assert camp.caption_problems(c, "nice #HighlightzCup #clips @highlightz") == []
    assert camp.caption_problems(c, "#HighlightzCupFinal #clips") == ["#HighlightzCup", "@highlightz"]
    assert camp.caption_problems(c, "#highlightzcup, #CLIPS. @Highlightz!") == []


# ── the API ──────────────────────────────────────────────────────────────────

PEOPLE = {
    "boss": {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
    "pro":  {"id": "pro", "username": "pro", "plan": "pro", "subscription_status": "active"},
}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: PEOPLE.get(uid))
    monkeypatch.setattr(user_store, "_load", lambda: [dict(u) for u in PEOPLE.values()])
    monkeypatch.setattr(user_store, "_save", lambda u: None)
    monkeypatch.setattr(api.settings, "campaigns_enabled", False)
    sent = []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        p = PEOPLE[uid]
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": p.get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c
    c.login, c.sent, c.api = login, sent, api
    return c


def test_only_admins_create_edit_delete_and_every_change_is_broadcast(client):
    assert client.login("pro").post("/admin/campaigns", json=form()).status_code in (401, 403)
    r = client.login("boss").post("/admin/campaigns", json=form())
    assert r.status_code == 200, r.text
    cid = r.json()["id"]
    assert ("campaigns_changed", None) in client.sent
    assert client.login("boss").put(f"/admin/campaigns/{cid}", json=form(title="Renamed")).json()["title"] == "Renamed"
    assert client.login("boss").post(f"/admin/campaigns/{cid}/image",
                                     files={"file": ("x.png", PNG, "image/png")}).status_code == 200
    assert client.login("pro").delete(f"/admin/campaigns/{cid}").status_code in (401, 403)
    assert client.login("boss").delete(f"/admin/campaigns/{cid}").status_code == 200
    assert camp.get(cid) is None


def test_a_bad_form_says_why(client):
    r = client.login("boss").post("/admin/campaigns", json=form(end_at=NOW - 7200))
    assert r.status_code == 400 and "after the start" in r.json()["detail"]


def test_the_marketplace_is_admin_only_until_switched_on(client, monkeypatch):
    live = camp.create(form())
    draft = camp.create(form(title="Secret", published=False))
    assert client.login("pro").get("/campaigns").status_code == 404
    ids = [r["id"] for r in client.login("boss").get("/campaigns").json()["rows"]]
    assert set(ids) == {live["id"], draft["id"]}, "an admin sees drafts too"
    monkeypatch.setattr(client.api.settings, "campaigns_enabled", True)
    rows = client.login("pro").get("/campaigns").json()["rows"]
    assert [r["id"] for r in rows] == [live["id"]], "a draft reached a user"
    assert client.login("pro").get(f"/campaigns/{draft['id']}").status_code == 404


def test_the_picture_is_served_to_those_who_can_see_it(client, monkeypatch):
    c = camp.create(form())
    camp.set_image(c["id"], PNG)
    assert client.login("pro").get(f"/campaigns/{c['id']}/image").status_code == 404
    r = client.login("boss").get(f"/campaigns/{c['id']}/image")
    assert r.status_code == 200 and r.content == PNG


# ── posting for a campaign ───────────────────────────────────────────────────

@pytest.fixture
def poster_env(client, tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(connections, "_INDEX", tmp_path / "publish_connections.json")
    connections._conns.clear(); connections._loaded = False
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json")
    sched._items.clear(); sched._loaded = False
    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    lib.load()
    monkeypatch.setattr(client.api.settings, "uploads_enabled", True)
    monkeypatch.setattr(poster, "start_now", lambda item, notify=None: True)
    for p in ("tiktok", "instagram", "youtube"):
        connections.save(connections.Connection(user_id="boss", platform=p, account_id="a",
                                                account_name="Nova", access_token="AT", refresh_token="RT"))

    async def gen():
        yield MP4
    up = asyncio.run(lib.save_stream("boss", "clip.mp4", gen(), source="clip"))

    async def _into_library(clip_id, uid):
        return up
    monkeypatch.setattr(client.api, "_clip_into_library", _into_library)
    client.api._clips["c1"] = {"id": "c1", "user_id": "boss", "channel": "jynxzi", "clip_title": "Ace",
                               "duration_seconds": 30}
    yield client
    client.api._clips.pop("c1", None)
    connections._conns.clear(); connections._loaded = False
    sched._items.clear(); sched._loaded = False
    lib._uploads.clear()


def _post(c, cid, platforms, caption):
    return c.login("boss").post("/publish/post-now", json={
        "clip_id": "c1", "platforms": platforms, "caption": caption, "campaign_id": cid})


def test_a_post_missing_a_required_tag_is_refused(poster_env):
    c = camp.create(form())
    r = _post(poster_env, c["id"], ["instagram"], "Ace #clips")
    assert r.status_code == 400 and "#HighlightzCup" in r.json()["detail"] and "@highlightz" in r.json()["detail"]
    assert sched.for_campaign(c["id"]) == []


def test_a_platform_the_campaign_does_not_count_is_refused(poster_env):
    c = camp.create(form())
    r = _post(poster_env, c["id"], ["youtube"], "Ace #HighlightzCup #clips @highlightz")
    assert r.status_code == 400 and "YouTube" in r.json()["detail"]


def test_a_campaign_that_is_not_live_is_refused(poster_env):
    c = camp.create(form(start_at=NOW + 86400, end_at=NOW + 2 * 86400))
    r = _post(poster_env, c["id"], ["instagram"], "Ace #HighlightzCup #clips @highlightz")
    assert r.status_code == 400 and "not started" in r.json()["detail"]


def test_a_good_post_is_recorded_as_an_entry(poster_env):
    c = camp.create(form())
    r = _post(poster_env, c["id"], ["instagram"], "Ace | from jynxzi #HighlightzCup #clips @highlightz")
    assert r.status_code == 202, r.text
    items = sched.for_campaign(c["id"])
    assert len(items) == 1 and items[0].campaign_id == c["id"]
    ent = poster_env.login("boss").get(f"/admin/campaigns/{c['id']}/entries").json()["rows"]
    assert ent[0]["platforms"] == ["instagram"] and "#HighlightzCup" in ent[0]["caption"]
    rows = poster_env.login("boss").get("/admin/campaigns").json()["rows"]
    assert rows[0]["entries"] == 1


def test_a_post_without_a_campaign_is_unchanged(poster_env):
    r = poster_env.login("boss").post("/publish/post-now", json={
        "clip_id": "c1", "platforms": ["instagram"], "caption": "anything"})
    assert r.status_code == 202 and sched.for_user("boss")[0].campaign_id == ""


# ── the pages ────────────────────────────────────────────────────────────────

def test_the_dashboard_is_wired_live():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    assert "{id:'campaigns',label:'Campaigns',icon:'trophy',campaignsOnly:true}" in page
    assert "(!n.campaignsOnly||canCampaignsFor(me))" in page
    assert "msg.event==='campaigns_changed'" in page
    ra = page[page.index("const refetchAll"):page.index("const wsBootstrapped")]
    assert "if(canCampaignsFor(data)) refetchCampaigns();" in ra
    assert "function CampaignsScreen(" in page and "function CampaignPage(" in page
    dlg = page[page.index("function PostNowDialog("):page.index("function PostNowDialog(") + 9000]
    assert "campaign_id: camp ? camp.id : undefined" in dlg and "campMissing.length === 0" in dlg


def test_the_admin_page_can_make_one_with_a_picture_and_rules():
    from src.dashboard import api
    html = api.ADMIN_HTML
    assert 'data-tab="campaigns"' in html and 'id="panel-campaigns"' in html
    for field in ('name="title"', 'id="cp-file"', 'name="rules"', 'name="start"', 'name="end"',
                  'name="prize_pool"', 'id="cp-add-payout"', 'id="cp-add-streamer"', 'name="hashtags"',
                  'name="caption_template"', 'name="published"', 'id="cp-judging"'):
        assert field in html, field
    assert "'/admin/campaigns/' + c.id + '/image'" in html
