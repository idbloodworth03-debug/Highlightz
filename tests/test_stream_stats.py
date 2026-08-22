"""Per-channel clip counts.

These numbers are meant to be SHOWN TO A STREAMER as evidence, which makes
undercounting the worst possible failure — a channel where 40 were caught and
30 rejected must not read as "10 caught". That is exactly what counting `_clips`
would produce, since rejecting deletes the clip.
"""

import json
import time

import pytest

from src.stats import stream_stats as ss


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stream_stats.jsonl")
    yield tmp_path


def clip(uid="u1", channel="pokimane", at=1000.0, cid="c1"):
    return {"id": cid, "user_id": uid, "channel": channel, "created_at": at}


def test_a_rejected_clip_still_counts_as_caught():
    """THE reason this ledger exists. Rejecting deletes the clip from _clips,
    so any count taken from there reports survivors only."""
    for i in range(4):
        ss.record(ss.CAUGHT, clip(cid=f"c{i}", at=1000.0 + i))
    ss.record(ss.APPROVED, clip(cid="c0", at=1000.0))
    for i in (1, 2, 3):
        ss.record(ss.REJECTED, clip(cid=f"c{i}", at=1000.0 + i))

    ch = ss.for_channel("u1", "pokimane")
    assert ch["caught"] == 4, "caught count collapsed to the survivors"
    assert ch["approved"] == 1 and ch["rejected"] == 3
    assert ch["kept_pct"] == 25


def test_clips_that_aged_out_are_separated_from_ones_you_rejected():
    """'You threw it away' and 'you never looked' are different facts about the
    product, and only one of them is a judgement on the clip."""
    for i in range(10):
        ss.record(ss.CAUGHT, clip(cid=f"c{i}", at=1000.0 + i))
    ss.record(ss.APPROVED, clip(cid="c0"))
    ss.record(ss.REJECTED, clip(cid="c1"))
    for i in range(2, 10):
        ss.record(ss.EXPIRED, clip(cid=f"c{i}"))

    ch = ss.for_channel("u1", "pokimane")
    assert ch["expired"] == 8
    assert ch["kept_pct"] == 10                 # 1 of 10 caught
    assert ch["kept_of_reviewed_pct"] == 50     # 1 of the 2 actually reviewed


def test_channels_are_kept_apart():
    ss.record(ss.CAUGHT, clip(channel="alice"))
    ss.record(ss.CAUGHT, clip(channel="bob"))
    ss.record(ss.APPROVED, clip(channel="bob"))
    assert ss.for_channel("u1", "alice")["approved"] == 0
    assert ss.for_channel("u1", "bob")["approved"] == 1


def test_one_users_numbers_never_include_anothers():
    ss.record(ss.CAUGHT, clip(uid="mine"))
    ss.record(ss.CAUGHT, clip(uid="theirs"))
    assert ss.for_channel("mine", "pokimane")["caught"] == 1


def test_a_channel_lookup_is_case_insensitive():
    """Twitch logins are lowercase but people type them however they like."""
    ss.record(ss.CAUGHT, clip(channel="Pokimane"))
    assert ss.for_channel("u1", "pokimane")["caught"] == 1


# ── sessions ─────────────────────────────────────────────────────────────────

def test_clips_hours_apart_are_split_into_separate_streams():
    day1 = 1_700_000_000.0
    day2 = day1 + 30 * 3600
    for i in range(3):
        ss.record(ss.CAUGHT, clip(cid=f"a{i}", at=day1 + i * 600))
    ss.record(ss.APPROVED, clip(cid="a0", at=day1))
    for i in range(2):
        ss.record(ss.CAUGHT, clip(cid=f"b{i}", at=day2 + i * 600))

    sessions = ss.for_channel("u1", "pokimane")["sessions"]
    assert len(sessions) == 2, f"gap clustering produced {len(sessions)} sessions"
    newest, oldest = sessions
    assert newest["started_at"] > oldest["started_at"], "sessions not newest-first"
    assert (oldest["caught"], oldest["approved"]) == (3, 1)
    assert newest["caught"] == 2


def test_a_long_stream_stays_one_session():
    """Breaks inside a broadcast are shorter than the gap threshold."""
    t = 1_700_000_000.0
    for i in range(6):
        ss.record(ss.CAUGHT, clip(cid=f"c{i}", at=t + i * 3000))   # 50 min apart
    assert len(ss.for_channel("u1", "pokimane")["sessions"]) == 1


def test_an_approval_days_later_belongs_to_the_session_it_was_caught_in():
    """The record is about the STREAM, so reviewing on Friday must not create a
    Friday session for a Tuesday clip."""
    caught_at = 1_700_000_000.0
    ss.record(ss.CAUGHT, clip(cid="c1", at=caught_at))
    time.sleep(0.01)
    ss.record(ss.APPROVED, clip(cid="c1", at=caught_at))   # clip_at, not now
    sessions = ss.for_channel("u1", "pokimane")["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["approved"] == 1


# ── robustness ───────────────────────────────────────────────────────────────

def test_a_corrupt_line_does_not_lose_the_rest():
    ss.record(ss.CAUGHT, clip())
    with ss._LOG_FILE.open("a") as f:
        f.write("{not json\n")
    ss.record(ss.CAUGHT, clip(cid="c2"))
    assert ss.for_channel("u1", "pokimane")["caught"] == 2


def test_writing_never_raises_even_on_junk():
    """Telemetry must not be able to break the clip pipeline."""
    ss.record(ss.CAUGHT, {})                 # no user, no channel
    ss.record(ss.CAUGHT, {"user_id": "u"})   # no channel
    assert ss.for_user("u1") == []


def test_no_data_reports_nothing_rather_than_zeroes_for_a_channel():
    assert ss.for_user("nobody") == []
    assert ss.for_channel("nobody", "anyone") is None


def test_deleting_an_account_removes_its_rows_and_keeps_everyone_elses():
    ss.record(ss.CAUGHT, clip(uid="leaving"))
    ss.record(ss.CAUGHT, clip(uid="staying"))
    assert ss.delete_all_for_user("leaving") == 1
    assert ss.for_user("leaving") == []
    assert ss.for_user("staying")[0]["caught"] == 1


# ── the hooks ────────────────────────────────────────────────────────────────

def test_approve_and_reject_both_reach_the_ledger(tmp_path, monkeypatch):
    """A ledger with a missing hook is worse than none: it would show a keep
    rate of 100% because only approvals ever got written."""
    import base64, json as _j
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    from src.dashboard import api
    from src.auth import users as user_store
    from src.profiles import manager as pm

    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "s.jsonl")
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "subscription_status": "active",
                                     "plan": "pro", "username": "u"})

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)

    class _PM:
        async def load(self, ch): return None
    monkeypatch.setattr(pm, "get_profile_manager", lambda uid: _PM())

    api._clips.clear()
    for cid in ("keep", "bin"):
        api._clips[cid] = {"id": cid, "user_id": "u1", "channel": "pokimane",
                           "status": "pending", "created_at": 1000.0}

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "u1", "subscription_status": "active"}).encode())).decode())

    assert c.post("/clips/keep/approve").status_code == 200
    assert c.post("/clips/bin/reject").status_code == 200
    api._clips.clear()

    ch = ss.for_channel("u1", "pokimane")
    assert ch is not None, "neither outcome reached the ledger"
    assert ch["approved"] == 1, "approve does not record"
    assert ch["rejected"] == 1, "reject does not record — keep rate would read 100%"


def test_every_clip_outcome_has_a_hook():
    """Guard on the wiring itself. The four places a clip's fate is decided are
    creation, approve, reject and cap-eviction; a missing one silently skews the
    number that gets shown to a streamer."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api)
    for marker in ("stream_stats.CAUGHT", "stream_stats.APPROVED",
                   "stream_stats.REJECTED", "_ss.MISSED"):
        assert marker in src, f"no hook records {marker}"


# ── admin only ───────────────────────────────────────────────────────────────

def test_the_clip_record_endpoint_is_admin_only(tmp_path, monkeypatch):
    """It spans EVERY user's channels. Reachable without the admin flag it
    would hand any signed-in user a list of everyone else's monitored streamers
    and how well the product performs on them."""
    import base64, json as _j
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    from src.dashboard import api
    from src.auth import users as user_store

    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "s.jsonl")
    people = {"boss":  {"id": "boss", "username": "boss", "is_admin": True,
                        "subscription_status": "active"},
              "punter": {"id": "punter", "username": "punter",
                         "subscription_status": "active"}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(user_store, "get_all", lambda: list(people.values()))
    ss.record(ss.CAUGHT, clip(uid="punter", channel="secretstreamer"))

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def as_user(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "is_admin": people[uid].get("is_admin", False),
             "subscription_status": "active"}).encode())).decode())
        return c.get("/admin/stream-stats")

    assert as_user("punter").status_code == 403, \
        "a non-admin can read every user's channels"
    r = as_user("boss")
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert rows and rows[0]["channel"] == "secretstreamer"
    assert rows[0]["username"] == "punter", "rows are not attributed to a user"


def test_the_user_facing_endpoint_is_gone():
    """Moved to admin by request. Leaving the old route behind would keep the
    data one URL away from any signed-in user."""
    from src.dashboard import api
    paths = {getattr(r, "path", "") for r in api.app.routes}
    assert "/stats/streams" not in paths
    assert "/admin/stream-stats" in paths


def test_all_rows_and_for_user_agree_on_the_same_numbers():
    """They share _summarise so the admin table and any future per-user view
    cannot drift apart."""
    for i in range(5):
        ss.record(ss.CAUGHT, clip(cid=f"c{i}", at=1000.0 + i))
    ss.record(ss.APPROVED, clip(cid="c0", at=1000.0))
    mine = ss.for_channel("u1", "pokimane")
    admin = [r for r in ss.all_rows()
             if r["user_id"] == "u1" and r["channel"] == "pokimane"][0]
    for k in ("caught", "approved", "rejected", "expired", "kept_pct"):
        assert mine[k] == admin[k], f"{k} differs between the two views"


# ── cleared, and undo ────────────────────────────────────────────────────────
# TWO EVENTS WERE BEING WRITTEN AND READ BY NOTHING.
#
# CLEARED has been recorded since the clear-queue button shipped. The key was
# missing from the session dict, so `ev in cur` was False and every cleared
# clip fell on the floor — the constant's own comment says it is recorded "so
# CAUGHT still reconciles against the sum of its outcomes", which it could not.
#
# UNDONE's comment promised that "anything that reads the log [is] able to
# correct for it". Nothing did. An undone reject counted as a rejection for
# ever, inflating rejections and dragging down every keep rate derived from
# them — numbers shown to streamers as evidence.

def test_a_cleared_clip_is_counted():
    ss.record(ss.CAUGHT, clip(cid="c1"))
    ss.record(ss.CLEARED, clip(cid="c1"))
    ch = ss.for_channel("u1", "pokimane")
    assert ch["cleared"] == 1, "cleared clips are being dropped on the floor again"


def test_clearing_is_not_counted_as_a_rejection():
    """Clearing the queue says nothing about whether the formula was right.
    Folding it into rejections would punish a channel for a user tidying up."""
    ss.record(ss.CAUGHT, clip(cid="c1"))
    ss.record(ss.CLEARED, clip(cid="c1"))
    ch = ss.for_channel("u1", "pokimane")
    assert ch["rejected"] == 0
    assert ch["kept_of_reviewed_pct"] == 0 and ch["approved"] == 0


def test_every_caught_clip_reconciles_against_its_outcomes():
    """The property CLEARED was recorded to preserve, asserted at last."""
    for i in range(5):
        ss.record(ss.CAUGHT, clip(cid=f"c{i}", at=1000.0 + i))
    ss.record(ss.APPROVED, clip(cid="c0", at=1000.0))
    ss.record(ss.REJECTED, clip(cid="c1", at=1001.0))
    ss.record(ss.CLEARED, clip(cid="c2", at=1002.0))
    ss.record(ss.CLEARED, clip(cid="c3", at=1003.0))
    ss.record(ss.EXPIRED, clip(cid="c4", at=1004.0))
    ch = ss.for_channel("u1", "pokimane")
    outcomes = ch["approved"] + ch["rejected"] + ch["cleared"] + ch["expired"]
    assert outcomes == ch["caught"] == 5, (
        f"{ch['caught']} caught but only {outcomes} accounted for — an outcome "
        f"is being dropped")


def test_an_undone_rejection_stops_counting_as_one():
    ss.record(ss.CAUGHT, clip(cid="c1"))
    ss.record(ss.REJECTED, clip(cid="c1"))
    ss.record(ss.UNDONE, clip(cid="c1"))
    ch = ss.for_channel("u1", "pokimane")
    assert ch["rejected"] == 0, "an action that was taken back is still counted"
    assert ch["caught"] == 1, "undoing a reject must not un-catch the clip"


def test_an_undone_clear_stops_counting_too():
    ss.record(ss.CAUGHT, clip(cid="c1"))
    ss.record(ss.CLEARED, clip(cid="c1"))
    ss.record(ss.UNDONE, clip(cid="c1"))
    assert ss.for_channel("u1", "pokimane")["cleared"] == 0


def test_undoing_one_clip_does_not_retract_another():
    ss.record(ss.CAUGHT, clip(cid="c1", at=1000.0))
    ss.record(ss.CAUGHT, clip(cid="c2", at=1001.0))
    ss.record(ss.REJECTED, clip(cid="c1", at=1000.0))
    ss.record(ss.REJECTED, clip(cid="c2", at=1001.0))
    ss.record(ss.UNDONE, clip(cid="c1", at=1000.0))
    assert ss.for_channel("u1", "pokimane")["rejected"] == 1


def test_the_keep_rate_reflects_the_undo():
    """The number a streamer is shown. An undone reject that still counted made
    the detector look worse than it was."""
    for i in range(4):
        ss.record(ss.CAUGHT, clip(cid=f"c{i}", at=1000.0 + i))
    ss.record(ss.APPROVED, clip(cid="c0", at=1000.0))
    ss.record(ss.REJECTED, clip(cid="c1", at=1001.0))
    ss.record(ss.REJECTED, clip(cid="c2", at=1002.0))
    ss.record(ss.UNDONE, clip(cid="c2", at=1002.0))
    ch = ss.for_channel("u1", "pokimane")
    assert (ch["approved"], ch["rejected"]) == (1, 1)
    assert ch["kept_of_reviewed_pct"] == 50


# ── the admin totals ─────────────────────────────────────────────────────────

def test_totals_by_user_sums_every_channel():
    ss.record(ss.CAUGHT, clip(channel="a", cid="c1"))
    ss.record(ss.APPROVED, clip(channel="a", cid="c1"))
    ss.record(ss.CAUGHT, clip(channel="b", cid="c2"))
    ss.record(ss.REJECTED, clip(channel="b", cid="c2"))
    ss.record(ss.CAUGHT, clip(channel="b", cid="c3"))
    ss.record(ss.CLEARED, clip(channel="b", cid="c3"))
    t = ss.totals_by_user()["u1"]
    assert (t["caught"], t["approved"], t["rejected"], t["cleared"]) == (3, 1, 1, 1)


def test_totals_by_user_keeps_users_apart():
    ss.record(ss.CAUGHT, clip(uid="u1", cid="c1"))
    ss.record(ss.APPROVED, clip(uid="u1", cid="c1"))
    ss.record(ss.CAUGHT, clip(uid="u2", cid="c2"))
    ss.record(ss.REJECTED, clip(uid="u2", cid="c2"))
    t = ss.totals_by_user()
    assert t["u1"]["approved"] == 1 and t["u1"]["rejected"] == 0
    assert t["u2"]["rejected"] == 1 and t["u2"]["approved"] == 0


def test_totals_by_user_corrects_for_undo():
    ss.record(ss.CAUGHT, clip(cid="c1"))
    ss.record(ss.REJECTED, clip(cid="c1"))
    ss.record(ss.UNDONE, clip(cid="c1"))
    assert ss.totals_by_user()["u1"]["rejected"] == 0


def test_an_undo_cannot_cross_channels():
    """The retraction set is built per channel. Built globally, a clip id reused
    on another channel would cancel the wrong row."""
    ss.record(ss.CAUGHT, clip(channel="a", cid="dup"))
    ss.record(ss.REJECTED, clip(channel="a", cid="dup"))
    ss.record(ss.CAUGHT, clip(channel="b", cid="dup"))
    ss.record(ss.REJECTED, clip(channel="b", cid="dup"))
    ss.record(ss.UNDONE, clip(channel="a", cid="dup"))
    t = ss.totals_by_user()["u1"]
    assert t["rejected"] == 1, "the undo on channel a retracted channel b's row"


def test_totals_agree_with_the_per_channel_view():
    """The admin table and a user's own stats must never disagree about the
    same person."""
    for i in range(6):
        ss.record(ss.CAUGHT, clip(channel="a", cid=f"c{i}", at=1000.0 + i))
    ss.record(ss.APPROVED, clip(channel="a", cid="c0", at=1000.0))
    ss.record(ss.REJECTED, clip(channel="a", cid="c1", at=1001.0))
    ss.record(ss.CLEARED, clip(channel="a", cid="c2", at=1002.0))
    ss.record(ss.UNDONE, clip(channel="a", cid="c2", at=1002.0))
    per = ss.for_channel("u1", "a")
    tot = ss.totals_by_user()["u1"]
    for k in ("caught", "approved", "rejected", "cleared", "expired"):
        assert per[k] == tot[k], f"{k} disagrees: {per[k]} vs {tot[k]}"


def test_totals_on_an_empty_ledger_are_empty_not_an_error():
    assert ss.totals_by_user() == {}
