"""Campaign results: leaderboard, winners and payouts (owner, 2026-10-23: "lets
add 3 and 4").

What these defend:
  1. what counts: posted, on the campaign's platforms, inside its dates, not
     disqualified; and each judging rule scores the way it says;
  2. ties go to whoever posted first;
  3. counts are validated, a disqualification needs a reason, and nothing
     can be changed while winners stand;
  4. winners: only after the end, one place each, only clippers with an
     entry that counts; empty places are the unwon prizes; reopening is
     refused once money has moved;
  5. payouts: only a winner gives details, only they and admins see them,
     and they go with the account;
  6. privacy of the leaderboard: names and scores, never another clipper's
     links, id or payout details;
  7. the API: admin-only, every change broadcast, winners and the streamer
     told, and the marketplace listing carries each viewer's board.
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
REAL_USER_HELPERS = (_user_store.display_names, _user_store.version)   # before any fixture swaps them
PEOPLE = {
    "boss": {"id": "boss", "username": "boss", "is_admin": True, "subscription_status": "active"},
    "ann": {"id": "ann", "username": "ann", "twitch_login": "ann_clips", "subscription_status": "active"},
    "bob": {"id": "bob", "username": "bob", "subscription_status": "active"},
    "cat": {"id": "cat", "username": "cat", "subscription_status": "active"},
    "streamr": {"id": "streamr", "username": "streamr", "twitch_login": "streamr",
                "email": "s@example.com", "subscription_status": "none"},
}


def campaign(**kw):
    base = {"title": "Clip Cup", "summary": "s", "rules": "r", "start_at": NOW - 10 * 86400,
            "end_at": NOW - 86400, "streamers": [{"name": "streamr", "platform": "twitch"}],
            "prize_pool": 600, "payouts": [300, 200, 100], "judging": "top_clip_views", "judging_n": 2,
            "platforms": ["tiktok", "youtube"], "published": True}
    base.update(kw)
    return camp.create(base)


def post(c, user, platform="tiktok", at=None, status="posted", url=None):
    """A clip posted for the campaign through Highlightz."""
    it = sched.Item(id=f"it{len(sched._items) + 1:04d}", user_id=user, upload_id="u", filename="f.mp4",
                    caption="#cup", platforms=[platform], status="posted", campaign_id=c["id"])
    it.results = {platform: {"status": status, "url": url or f"https://www.tiktok.com/@{user}/video/{len(sched._items)}",
                             "at": NOW - 5 * 86400 if at is None else at}}
    sched._items[it.id] = it
    sched._save()           # as every real change to the queue does
    return f"{it.id}:{platform}"


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
    # Changes when PEOPLE does, as the real one does when users.json changes.
    monkeypatch.setattr(user_store, "version", lambda: hash(json.dumps(PEOPLE, sort_keys=True)))
    monkeypatch.setattr(user_store, "admin_ids", lambda: frozenset(i for i, p in PEOPLE.items() if p.get("is_admin")))
    for m, store in ((camp, camp._campaigns), (creq, creq._requests), (res, res._results), (sched, sched._items),
                     (res, res._memo), (joins, joins._joins)):
        store.clear()
        m._loaded = True
    yield
    for m, store in ((camp, camp._campaigns), (creq, creq._requests), (res, res._results), (sched, sched._items),
                     (res, res._memo), (joins, joins._joins)):
        store.clear()
        m._loaded = False


def counts(c, **by_key):
    res.save_stats(c, [{"key": k, **v} for k, v in by_key.items()], "boss")


# ── 1/2. what counts and how it scores ───────────────────────────────────────

def test_only_entries_that_meet_the_rules_count():
    c = campaign()
    ok = post(c, "ann")
    early = post(c, "bob", at=NOW - 20 * 86400)
    failed = post(c, "bob", status="failed")
    wrong = post(c, "cat", platform="instagram")
    why = {e["key"]: e["why_not"] for e in res.entries(c)}
    assert why[ok] == "" and why[early] == "posted outside the campaign dates"
    assert why[failed] == "not posted" and why[wrong] == "platform not in this campaign"
    assert [r["name"] for r in res.standings(c)] == ["ann_clips"]


def test_top_clip_views_is_the_best_single_clip():
    c = campaign(judging="top_clip_views")
    a1, a2 = post(c, "ann"), post(c, "ann")
    b1 = post(c, "bob")
    counts(c, **{a1: {"views": 900}, a2: {"views": 800}, b1: {"views": 1000}})
    rows = res.standings(c)
    assert [(r["name"], r["score"]) for r in rows] == [("bob", 1000), ("ann_clips", 900)]


def test_best_n_views_adds_up_the_best_n():
    c = campaign(judging="best_n_views", judging_n=2)
    a = [post(c, "ann") for _ in range(3)]
    b1 = post(c, "bob")
    counts(c, **{a[0]: {"views": 500}, a[1]: {"views": 400}, a[2]: {"views": 300}, b1: {"views": 1000}})
    assert [(r["name"], r["score"]) for r in res.standings(c)] == [("bob", 1000), ("ann_clips", 900)]


def test_engagement_adds_likes_and_comments():
    c = campaign(judging="best_n_engagement", judging_n=1)
    a1, b1 = post(c, "ann"), post(c, "bob")
    counts(c, **{a1: {"views": 100, "likes": 50, "comments": 10}, b1: {"views": 150}})
    assert [(r["name"], r["score"]) for r in res.standings(c)] == [("ann_clips", 160), ("bob", 150)]
    assert res.board(c, "ann")["metric"] == "engagement"


def test_a_tie_goes_to_whoever_posted_first():
    c = campaign()
    late = post(c, "ann", at=NOW - 3 * 86400)
    early = post(c, "bob", at=NOW - 4 * 86400)
    counts(c, **{late: {"views": 500}, early: {"views": 500}})
    assert [r["name"] for r in res.standings(c)] == ["bob", "ann_clips"]


def test_a_disqualified_entry_stops_counting():
    c = campaign()
    a1, b1 = post(c, "ann"), post(c, "bob")
    counts(c, **{a1: {"views": 9000, "disqualified": True, "reason": "bought views"}, b1: {"views": 10}})
    assert [r["name"] for r in res.standings(c)] == ["bob"]
    e = next(e for e in res.entries(c) if e["key"] == a1)
    assert e["why_not"] == "disqualified: bought views"


# ── 1b. an entry stays an entry ──────────────────────────────────────────────

def _land(c, user, platform="tiktok"):
    """A post going out for the campaign the way the poster does it."""
    it = sched.add(user, "u" + user, "f.mp4", "#cup", [platform], campaign_id=c["id"])
    it = sched.mark_result(it.id, user, platform, sched.R_POSTED, url=f"https://www.tiktok.com/@{user}/video/1",
                           remote_id="v1")
    res.record_entry(it, platform)
    return it, f"{it.id}:{platform}"


def test_tidying_the_queue_or_deleting_the_clip_keeps_the_entry():
    c = campaign(start_at=NOW - 86400, end_at=NOW + 86400)
    it, key = _land(c, "ann")
    other, key2 = _land(c, "bob")
    counts(c, **{key: {"views": 700}, key2: {"views": 300}})
    assert sched.remove(it.id, "ann") and sched.drop_upload(other.upload_id, "bob")
    assert [(r["name"], r["score"]) for r in res.standings(c)] == [("ann_clips", 700), ("bob", 300)]
    res._loaded = False
    res._results.clear()
    assert len(res.entries(c)) == 2, "the kept entries did not survive a reload"


def test_a_post_moved_to_another_campaign_still_counts_for_the_one_it_went_out_for():
    a = campaign(title="A", start_at=NOW - 86400, end_at=NOW + 86400)
    b = campaign(title="B", start_at=NOW - 86400, end_at=NOW + 86400)
    it, key = _land(a, "ann")
    # The same clip posted again elsewhere, for B: the item is re-tagged.
    sched.update(it.id, "ann", campaign_id=b["id"], platforms=["tiktok", "youtube"])
    assert it.results["tiktok"]["campaign_id"] == a["id"]
    assert [e["key"] for e in res.entries(a)] == [key]
    assert [e["platform"] for e in res.entries(b)] == ["youtube"], "A's post started counting for B"


def test_a_deleted_account_takes_its_entries_but_not_the_win():
    c = campaign(start_at=NOW - 3 * 86400, end_at=NOW + 86400)
    _it, key = _land(c, "ann")
    _land(c, "bob")
    counts(c, **{key: {"views": 900}})
    camp.update(c["id"], {**c, "end_at": time.time()})       # ended just now, after both posts
    c = camp.get(c["id"])
    res.confirm(c, res.propose(c), "boss")
    res.set_payee(c, "ann", "paypal", "ann@example.com")
    sched.delete_all_for_user("ann")
    res.forget_user("ann")
    assert [e["name"] for e in res.entries(c)] == ["bob"]
    assert key not in res._results[c["id"]]["stats"]
    v = res.admin_view(c)
    assert v["winners"][0]["name"] == "ann_clips" and v["winners"][0]["payee"] is None


def test_an_account_that_is_gone_drops_off_the_board(monkeypatch):
    """The admin's delete leaves the queue; its entries must not linger as
    "a clipper" who can never be paid."""
    c = campaign()
    _three(c)
    monkeypatch.delitem(PEOPLE, "cat")
    assert [r["name"] for r in res.standings(c)] == ["ann_clips", "bob"]


def test_deleting_an_account_takes_its_entries_off_every_board_live(client, monkeypatch):
    c = campaign(start_at=NOW - 86400, end_at=NOW + 86400)
    _land(c, "ann")
    post(c, "ann", at=NOW - 3600)          # one still in her queue
    _land(c, "bob")
    from src.auth import users as user_store
    from src.feedback import reviews
    from src.publish import connections
    from src.stats import downloads, stream_stats
    from src.uploads import library
    for mod in (library, connections, reviews, stream_stats, downloads):
        monkeypatch.setattr(mod, "delete_all_for_user", lambda uid: 0)
    monkeypatch.setattr(user_store, "delete", lambda uid: None)
    from src.dashboard import api
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    assert len(res.entries(c)) == 3
    client.sent.clear()
    assert client.login("ann").delete("/account").status_code == 200
    assert [e["name"] for e in res.entries(c)] == ["bob"]
    assert ("campaigns_changed", None) in [(e, u) for e, u, _ in client.sent], "the other boards were not told"


def test_the_poster_records_the_entry_and_tells_every_board(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from src.publish import poster, providers, connections
    from src.uploads import library as upload_lib
    c = campaign(start_at=NOW - 86400, end_at=NOW + 86400)
    it = sched.add("ann", "up1", "f.mp4", "#cup", ["youtube"], campaign_id=c["id"])
    f = sched._INDEX.parent / "f.mp4"
    f.write_bytes(b"x")
    monkeypatch.setattr(upload_lib, "get", lambda uid_, *_a: SimpleNamespace(id="up1"))
    monkeypatch.setattr(upload_lib, "path_for", lambda up: f)
    monkeypatch.setattr(connections, "connected_platforms", lambda uid: {"youtube"})
    monkeypatch.setattr(connections, "get", lambda uid, p: object())

    class Prov:
        async def post(self, *a, **k):
            return SimpleNamespace(url="https://youtu.be/x", remote_id="x", note="")
    monkeypatch.setattr(providers, "get", lambda p: Prov())

    async def fresh(*a):
        return None
    monkeypatch.setattr(providers, "ensure_fresh", fresh)
    sent = []

    async def notify(msg, uid):
        sent.append((msg["event"], uid))
    out = asyncio.run(poster.post_item(it, notify))
    assert out.results["youtube"]["status"] == "posted"
    assert [e["key"] for e in res.entries(c)] == [f"{it.id}:youtube"]
    assert ("campaigns_changed", None) in sent
    # A results store that cannot be written must not turn a landed post into
    # a failure (which would post it again).
    it2 = sched.add("bob", "up1", "f.mp4", "#cup", ["youtube"], campaign_id=c["id"])
    monkeypatch.setattr(res, "record_entry", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    out = asyncio.run(poster.post_item(it2, notify))
    assert out.results["youtube"]["status"] == "posted"


def test_a_campaign_with_confirmed_winners_cannot_be_deleted(client):
    c = campaign()
    _three(c)
    res.confirm(c, res.propose(c), "boss")
    r = client.login("boss").delete(f"/admin/campaigns/{c['id']}")
    assert r.status_code == 400 and "record of its prizes" in r.json()["detail"]
    res.reopen(c, "boss")
    assert client.login("boss").delete(f"/admin/campaigns/{c['id']}").status_code == 200
    assert c["id"] not in res._results, "a deleted campaign's counts and payout details stayed"


# ── 1c. worked out once per change, never stale (quick audit, 2026-10-24) ────

def _counting_builds(monkeypatch):
    built = []
    real = res._build_entries
    monkeypatch.setattr(res, "_build_entries", lambda c: built.append(c["id"]) or real(c))
    return built


def test_every_viewer_shares_one_build_until_something_changes(monkeypatch):
    c = campaign(start_at=NOW - 86400, end_at=NOW + 86400)
    _it, key = _land(c, "ann")
    _land(c, "bob")
    built = _counting_builds(monkeypatch)
    for viewer in ["ann", "bob", "cat"] * 30:
        res.board(c, viewer)
    res.summary(c)
    res.admin_view(c)
    assert len(built) == 1, "each viewer rebuilt the board"
    counts(c, **{key: {"views": 10}})                   # this store changes
    res.board(c, "ann")
    _land(c, "cat")                                     # the queue and this store change
    res.board(c, "ann")
    sched.add("bob", "u9", "f.mp4", "", ["tiktok"])     # the queue changes, not for this campaign
    res.board(c, "ann")
    assert len(built) == 4


def test_nothing_is_ever_served_stale(monkeypatch):
    c = campaign(start_at=NOW - 86400, end_at=NOW + 86400)
    _it, key = _land(c, "ann")
    other, key2 = _land(c, "bob")
    counts(c, **{key: {"views": 50}, key2: {"views": 40}})
    assert [r["name"] for r in res.board(c, "cat")["rows"]] == ["ann_clips", "bob"]
    # A post still in the queue appears (the queue's own counter moved).
    k3 = post(c, "cat", at=NOW - 3600)
    counts(c, **{k3: {"views": 99}})
    assert [r["name"] for r in res.board(c, "cat")["rows"]] == ["cat", "ann_clips", "bob"]
    # A renamed account shows its new name; a deleted one drops off.
    monkeypatch.setitem(PEOPLE, "bob", {**PEOPLE["bob"], "twitch_login": "bob_live"})
    assert [r["name"] for r in res.board(c, "cat")["rows"]] == ["cat", "ann_clips", "bob_live"]
    monkeypatch.delitem(PEOPLE, "ann")
    assert [r["name"] for r in res.board(c, "cat")["rows"]] == ["cat", "bob_live"]
    # The campaign's own dates move past every post: none counts any more.
    moved = {**c, "start_at": time.time() + 60}
    assert [r["name"] for r in res.board(moved, "cat")["rows"]] == []
    assert [r["name"] for r in res.board(c, "cat")["rows"]] == ["cat", "bob_live"]
    # Disqualifying, through this store.
    counts(c, **{k3: {"views": 99, "disqualified": True, "reason": "reupload"}})
    assert [r["name"] for r in res.board(c, "cat")["rows"]] == ["bob_live"]


def test_the_queue_index_follows_every_change():
    a = campaign(title="A")
    b = campaign(title="B")
    it = sched.add("ann", "up1", "f.mp4", "", ["tiktok"], campaign_id=a["id"])
    it2 = sched.add("bob", "up2", "f.mp4", "", ["tiktok"], campaign_id=a["id"])
    assert {i.id for i in sched.for_campaign(a["id"])} == {it.id, it2.id}
    sched.update(it.id, "ann", campaign_id=b["id"])
    assert [i.id for i in sched.for_campaign(a["id"])] == [it2.id]
    assert [i.id for i in sched.for_campaign(b["id"])] == [it.id]
    sched.remove(it2.id, "bob")
    assert sched.for_campaign(a["id"]) == []
    sched.drop_upload("up1", "ann")
    assert sched.for_campaign(b["id"]) == []
    it3 = sched.add("cat", "up3", "f.mp4", "", ["tiktok"], campaign_id=a["id"])
    sched.delete_all_for_user("cat")
    assert sched.for_campaign(a["id"]) == [] and it3.id not in sched._items
    sched._loaded = False                               # a reload from disk
    sched._items.clear()
    assert sched.for_campaign(a["id"]) == []


def test_the_users_file_names_and_its_version(tmp_path, monkeypatch):
    """The real helpers (the fixture stands in for them everywhere else)."""
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    names, version = REAL_USER_HELPERS
    user_store._save([{"id": "a", "username": "ann", "twitch_login": "ann_tw"},
                      {"id": "b", "username": "bob", "kick_slug": "bob_k"}, {"id": "c", "username": "cat"}])
    v1 = version()
    assert version() == v1, "unchanged file, new version"
    assert names(["a", "b", "c", "gone"]) == {"a": "ann_tw", "b": "bob_k", "c": "cat"}
    user_store._save([{"id": "a", "username": "ann"}])
    assert version() != v1
    assert names(["a", "b"]) == {"a": "ann"}


# ── 3. counting ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("row,msg", [
    ({"views": "lots"}, "whole number"),
    ({"views": -1}, "between 0"),
    ({"views": 10, "disqualified": True}, "say why"),
])
def test_counts_are_checked(row, msg):
    c = campaign()
    k = post(c, "ann")
    with pytest.raises(res.ResultsError, match="ann_clips on tiktok: .*" + msg):
        counts(c, **{k: row})


def test_one_bad_row_saves_none_of_them():
    c = campaign()
    a, b = post(c, "ann"), post(c, "bob")
    with pytest.raises(res.ResultsError, match="bob on tiktok"):
        res.save_stats(c, [{"key": a, "views": 500}, {"key": b, "views": "lots"}], "boss")
    assert not any(e["counted"] for e in res.entries(c)), "the good row was half-saved"
    res._loaded = False
    res._results.clear()
    assert not any(e["counted"] for e in res.entries(c))


def test_counts_take_commas_and_blank_means_not_counted():
    c = campaign()
    k = post(c, "ann")
    counts(c, **{k: {"views": "12,300", "likes": ""}})
    e = res.entries(c)[0]
    assert e["views"] == 12300 and e["likes"] is None and e["counted"]
    counts(c, **{k: {"views": ""}})
    assert not res.entries(c)[0]["counted"]


def test_an_entry_from_another_campaign_is_refused():
    c, other = campaign(), campaign(title="Other")
    k = post(other, "ann")
    with pytest.raises(res.ResultsError, match="not part of this campaign"):
        counts(c, **{k: {"views": 1}})


# ── 4. winners ───────────────────────────────────────────────────────────────

def _three(c):
    a, b, d = post(c, "ann"), post(c, "bob"), post(c, "cat")
    counts(c, **{a: {"views": 300}, b: {"views": 200}, d: {"views": 100}})


def test_the_proposal_follows_the_standings_and_empty_places_are_unwon():
    c = campaign(payouts=[300, 200, 100])
    post(c, "ann")
    k = post(c, "bob")
    counts(c, **{k: {"views": 50}})
    prop = res.propose(c)
    assert [(p["place"], p["name"], p["amount"]) for p in prop] == \
        [(1, "bob", 300), (2, "ann_clips", 200), (3, "", 100)]
    rec = res.confirm(c, prop, "boss")
    # An admin made this one: the unwon prize is recorded but owed to nobody.
    assert rec["refund"] == {"amount": 100.0, "status": "none", "reference": "", "at": 0, "by": ""}
    assert res.summary(c)["unwon"] == 0


def test_winners_only_after_the_end_and_by_the_rules():
    live = campaign(end_at=NOW + 86400)
    post(live, "ann")
    with pytest.raises(res.ResultsError, match="once the campaign has ended"):
        res.confirm(live, [{"place": 1, "user_id": "ann"}], "boss")
    c = campaign()
    _three(c)
    for picks, msg in (([{"place": 1, "user_id": "ann"}, {"place": 2, "user_id": "ann"}], "one place only"),
                       ([{"place": 1, "user_id": "ann"}, {"place": 1, "user_id": "bob"}], "two winners"),
                       ([{"place": 4, "user_id": "ann"}], "no place 4"),
                       ([{"place": 1, "user_id": "streamr"}], "no entry that counts")):
        with pytest.raises(res.ResultsError, match=msg):
            res.confirm(c, picks, "boss")
    rec = res.confirm(c, [{"place": 1, "user_id": "cat"}, {"place": 2, "user_id": "ann"},
                          {"place": 3, "user_id": "bob"}], "boss")
    assert [(w["place"], w["name"], w["amount"]) for w in rec["winners"]] == \
        [(1, "cat", 300), (2, "ann_clips", 200), (3, "bob", 100)], "an admin's choice was overridden"
    assert rec["refund"]["status"] == "none"
    with pytest.raises(res.ResultsError, match="frozen|confirmed"):
        counts(c, **{res.entries(c)[0]["key"]: {"views": 1}})


def test_reopening_is_refused_once_money_has_moved():
    c = campaign()
    _three(c)
    res.confirm(c, res.propose(c), "boss")
    res.mark_paid(c, 1, True, "boss", "paypal", "TX-1")
    with pytest.raises(res.ResultsError, match="Undo that first"):
        res.reopen(c, "boss")
    res.mark_paid(c, 1, False, "boss")
    res.reopen(c, "boss")
    assert not res.admin_view(c)["winners"] and res.admin_view(c)["proposal"]


def test_marking_paid_needs_a_method_and_refunds_need_something_unwon():
    c = campaign()
    _three(c)
    res.confirm(c, res.propose(c), "boss")
    with pytest.raises(res.ResultsError, match="how the prize was sent"):
        res.mark_paid(c, 1, True, "boss", "", "")
    with pytest.raises(res.ResultsError, match="nothing unwon"):
        res.mark_refunded(c, True, "boss", "re_1")


# ── 5. payout details ────────────────────────────────────────────────────────

def test_only_a_winner_gives_payout_details_and_they_go_with_the_account():
    c = campaign()
    _three(c)
    with pytest.raises(res.ResultsError, match="Only a winner"):
        res.set_payee(c, "ann", "paypal", "ann@example.com")
    res.confirm(c, res.propose(c), "boss")
    with pytest.raises(res.ResultsError, match="Only a winner"):
        res.set_payee(c, "streamr", "paypal", "x@example.com")
    with pytest.raises(res.ResultsError, match="how you want to be paid"):
        res.set_payee(c, "ann", "bitcoin", "x")
    res.set_payee(c, "ann", "paypal", "  ann@example.com ")
    assert res.board(c, "ann")["prize"]["payee"]["address"] == "ann@example.com"
    res.forget_user("ann")
    assert res.board(c, "ann")["prize"]["payee"] is None
    assert res.admin_view(c)["winners"][0]["name"] == "ann_clips", "the win itself was lost"


# ── 6. what each person sees ─────────────────────────────────────────────────

def test_the_board_shows_names_and_scores_and_nothing_private():
    c = campaign()
    _three(c)
    res.confirm(c, res.propose(c), "boss")
    res.set_payee(c, "ann", "paypal", "ann@example.com")
    b = res.board(c, "bob")
    text = json.dumps(b)
    assert [r["name"] for r in b["rows"]] == ["ann_clips", "bob", "cat"]
    assert b["you"]["rank"] == 2 and [r["you"] for r in b["rows"]] == [False, True, False]
    for leak in ("ann@example.com", "tiktok.com", '"ann"', "user_id"):
        assert leak not in text, f"the board leaks {leak}"
    assert b["prize"]["place"] == 2 and b["prize"]["payee"] is None
    assert [w["name"] for w in b["winners"]] == ["ann_clips", "bob", "cat"]


def test_the_streamer_sees_their_campaign_s_best_clips():
    c = campaign()
    _three(c)
    s = res.summary(c)
    assert (s["entries"], s["clippers"], s["views"], s["counted"]) == (3, 3, 600, 3)
    assert [t["views"] for t in s["top"]] == [300, 200, 100] and s["top"][0]["url"].startswith("https://")


def test_the_csv_has_every_entry_and_the_winners():
    c = campaign()
    _three(c)
    res.confirm(c, res.propose(c), "boss")
    res.mark_paid(c, 1, True, "boss", "paypal", "TX-9")
    import csv
    import io
    rows = list(csv.reader(io.StringIO(res.csv_text(c))))
    gap = rows.index([])
    assert len(rows[1:gap]) == 3 and all(r[2] == "tiktok" and r[8] == "yes" for r in rows[1:gap])
    assert rows[gap + 2][:3] == ["1", "ann_clips", "300.00"] and rows[gap + 2][-1] == "TX-9"


# ── 7. the API ───────────────────────────────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.auth import users as user_store
    from src.dashboard import api
    monkeypatch.setattr(user_store, "_load", lambda: [dict(u) for u in PEOPLE.values()])
    monkeypatch.setattr(user_store, "_save", lambda u: None)
    monkeypatch.setattr(api.settings, "campaigns_enabled", True)
    sent, emails = [], []

    async def _bcast(msg, user_id=None):
        sent.append((msg.get("event"), user_id, msg))
    monkeypatch.setattr(api, "broadcast", _bcast)
    monkeypatch.setattr(api, "_tell_owner_email", lambda s, t: emails.append((s, t)))
    c = TestClient(api.app, base_url="https://testserver")
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": PEOPLE[uid].get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c
    c.login, c.sent, c.emails = login, sent, emails
    return c


def test_only_admins_count_and_pick(client):
    c = campaign()
    _three(c)
    for path, body in ((f"/admin/campaigns/{c['id']}/stats", {"rows": []}),
                       (f"/admin/campaigns/{c['id']}/winners", {"winners": []}),
                       (f"/admin/campaigns/{c['id']}/payouts/1", {"paid": True}),
                       (f"/admin/campaigns/{c['id']}/refund", {"refunded": True})):
        assert client.login("ann").post(path, json=body).status_code == 403, path
    assert client.login("ann").get(f"/admin/campaigns/{c['id']}/results").status_code == 403
    assert client.login("ann").get(f"/admin/campaigns/{c['id']}/results.csv").status_code == 403


def test_the_whole_flow_through_the_api(client):
    c = campaign()
    a, b = post(c, "ann"), post(c, "bob")
    boss = client.login("boss")
    r = boss.post(f"/admin/campaigns/{c['id']}/stats", json={"rows": [
        {"key": a, "views": "1,500"}, {"key": b, "views": 900}]})
    assert r.status_code == 200 and r.json()["changed"] == 2
    assert ("campaigns_changed", None) in [(e, u) for e, u, _ in client.sent]
    # Every clipper's marketplace carries the board, live via campaigns_changed.
    row = next(x for x in client.login("bob").get("/campaigns").json()["rows"] if x["id"] == c["id"])
    assert row["board"]["you"]["rank"] == 2 and row["board"]["rows"][0]["name"] == "ann_clips"
    picks = client.login("boss").get(f"/admin/campaigns/{c['id']}/results").json()["proposal"]
    client.sent.clear()
    r = client.login("boss").post(f"/admin/campaigns/{c['id']}/winners", json={"winners": picks})
    assert r.status_code == 200, r.text
    notes = {u: m["message"] for e, u, m in client.sent if e == "campaign_notice"}
    assert set(notes) == {"ann", "bob"} and "1st place" in notes["ann"] and "$300.00" in notes["ann"]
    # The winner says where to send it; the owner is emailed, without the address.
    r = client.login("ann").post(f"/campaigns/{c['id']}/payout-details",
                                 json={"method": "paypal", "address": "ann@example.com"})
    assert r.status_code == 200 and ("campaigns_changed", "ann") in [(e, u) for e, u, _ in client.sent]
    assert client.emails and "ann@example.com" not in client.emails[-1][1]
    assert client.login("bob").post(f"/campaigns/{c['id']}/payout-details",
                                    json={"method": "venmo", "address": ""}).status_code == 400
    assert client.login("cat").post(f"/campaigns/{c['id']}/payout-details",
                                    json={"method": "paypal", "address": "c@x.com"}).status_code == 400
    view = client.login("boss").get(f"/admin/campaigns/{c['id']}/results").json()
    assert view["winners"][0]["payee"]["address"] == "ann@example.com"
    client.sent.clear()
    r = client.login("boss").post(f"/admin/campaigns/{c['id']}/payouts/1",
                                  json={"paid": True, "method": "paypal", "reference": "TX-1"})
    assert r.status_code == 200 and r.json()["winners"][0]["paid"]["reference"] == "TX-1"
    assert any(e == "campaign_notice" and u == "ann" and "was sent by PayPal" in m["message"]
               for e, u, m in client.sent)
    board = next(x for x in client.login("ann").get("/campaigns").json()["rows"] if x["id"] == c["id"])["board"]
    assert board["prize"]["paid"]["method"] == "PayPal" and board["winners"][0]["paid"] is True
    csv = client.login("boss").get(f"/admin/campaigns/{c['id']}/results.csv")
    assert csv.headers["content-type"].startswith("text/csv") and "TX-1" in csv.text


def test_the_streamer_hears_about_winners_and_the_refund(client, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "campaign_requests_enabled", True)
    monkeypatch.setattr(settings, "campaign_flat_fee_usd", 0.0)
    r = creq.submit(PEOPLE["streamr"], {
        "streamers": [{"name": "streamr", "platform": "twitch"}], "title": "Streamr week",
        "summary": "s", "brief": "Funny moments and clutch plays, under a minute.", "budget": 300,
        "payouts": [200, 100], "judging": "top_clip_views", "duration_days": 7, "platforms": ["tiktok"],
        "agree": True, "contact_email": "s@example.com"})
    creq.approve(r["id"], "boss")
    r, c = creq.publish(r["id"], payment="stripe", amount_cents=31500)
    camp.update(c["id"], {**c, "start_at": NOW - 8 * 86400, "end_at": NOW - 3600})
    c = camp.get(c["id"])
    k = post(c, "ann", at=NOW - 2 * 86400)
    client.login("boss").post(f"/admin/campaigns/{c['id']}/stats", json={"rows": [{"key": k, "views": 777}]})
    mine = client.login("streamr").get("/campaign-requests").json()["rows"][0]["results"]
    assert mine["views"] == 777 and mine["top"][0]["name"] == "ann_clips"
    client.sent.clear()
    client.login("boss").post(f"/admin/campaigns/{c['id']}/winners",
                              json={"winners": [{"place": 1, "user_id": "ann"}]})
    told = [m["message"] for e, u, m in client.sent if e == "campaign_request_updated" and u == "streamr"]
    assert told and "$100.00 of prizes went unwon" in told[0]
    client.sent.clear()
    client.login("boss").post(f"/admin/campaigns/{c['id']}/refund", json={"refunded": True, "reference": "re_1"})
    assert any("We refunded $100.00" in m["message"] for e, u, m in client.sent
               if e == "campaign_request_updated" and u == "streamr")
    assert client.login("streamr").get("/campaign-requests").json()["rows"][0]["results"]["refund_status"] == "refunded"


def test_the_dashboard_and_admin_page_are_wired():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    from src.dashboard import api
    ws = page[page.index("msg.event==='campaign_notice'"):][:600]
    assert "refetchCampaigns()" in ws and "flash(msg.message)" in ws
    assert "function CampaignBoard(" in page and "function CampaignPrize(" in page
    assert "'/campaigns/' + c.id + '/payout-details'" in page
    assert "function PromoteResults(" in page
    html = api.ADMIN_HTML
    for piece in ("'/admin/campaigns/' + id + '/results'", "'/admin/campaigns/' + CR_ID + '/stats'",
                  "'/admin/campaigns/' + CR_ID + '/winners'", "'/payouts/' + place", "'/winners/reopen'",
                  "'/refund'", "/results.csv", 'id="cp-results"', "cp-res"):
        assert piece in html, piece
    # The background re-read never lands on top of something typed.
    poll = html[html.index("if(!campaignsOpen()) return;"):][:200]
    assert "cpResults(CR_ID, true)" in poll
    quiet = html[html.index("async function cpResults("):][:700]
    assert "quiet && (CR_DIRTY || CR_BUSY)" in quiet
    # The payout methods a winner can pick are the ones the server accepts.
    import re
    js = page[page.index("const CAMP_PAYOUT = {"):][:600]
    assert set(re.findall(r"(\w+): \['", js)) == set(res.PAYOUT_METHODS)
