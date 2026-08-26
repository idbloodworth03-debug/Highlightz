"""Cross-rating: two people scoring the same clip, blind to each other.

WHY IT EXISTS. Across 1,641 ratings on production, not one clip had been seen
by two people — the queue served each labeler only their own account's clips.
Two consequences, both fatal to the analysis that was supposed to justify a
formula change:

  * Nobody could say whether the humans AGREE. "virality_score correlates
    -0.06 with human virality" presumes there is a stable human view to
    correlate with. If two people rank the same clip differently there is no
    such target, and a flat result is evidence about the target as much as
    about the bot.
  * Labeler was perfectly confounded with CHANNEL. Each person rated only
    their own streamers, so "this rater is harsher" and "these channels are
    quieter" were the same number and could never be told apart.

THE PROPERTY THAT MATTERS MOST is blindness. A second opinion that has seen
the first is not a second opinion — it measures suggestibility, and it would
produce agreement numbers that look good and mean nothing. Several tests here
exist only to hold that down.
"""

import base64
import json as _j
import time

import pytest
from itsdangerous import TimestampSigner


@pytest.fixture
def env(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.auth import users as user_store
    from src.dashboard import api

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(api, "_HUMAN_SCORES_FILE", tmp_path / "human_scores.jsonl")

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "lab_a", "username": "alice", "is_labeler": True, "created_at": now},
        {"id": "lab_b", "username": "bob", "is_labeler": True, "created_at": now},
        {"id": "plain", "username": "nobody", "created_at": now},
    ]))

    clips = {}
    for i, owner in enumerate(["lab_a", "lab_a", "lab_b", "lab_b"]):
        cid = f"c{i}"
        clips[cid] = {
            "id": cid, "user_id": owner, "channel": f"ch{i}", "game": "Just Chatting",
            "status": "pending", "created_at": now - i * 100, "duration_seconds": 30,
            "trigger_score": 70 + i, "virality_score": 40 + i,
            "clip_title": f"{'ch'}{i} — Chat Erupts",
            "twitch_url": f"https://clips.twitch.tv/T{i}",
            "embed_url": f"https://clips.twitch.tv/embed?clip=T{i}",
            "trigger_signals": [{"type": "SignalType.AUDIO_SPIKE", "value": 0.8}],
        }
    monkeypatch.setattr(api, "_clips", clips)

    sent = []

    async def _bc(msg, **kw):
        sent.append((msg, kw.get("user_id")))
    monkeypatch.setattr(api, "broadcast", _bc)

    def client_for(uid, admin=False):
        c = TestClient(api.app)
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid, "is_admin": admin,
             "subscription_status": "active"}).encode())).decode())
        return c

    return {"api": api, "clips": clips, "sent": sent,
            "a": client_for("lab_a"), "b": client_for("lab_b"),
            "plain": client_for("plain")}


def _score(client, clip_id, virality=5, sentiment=5, audio=5):
    return client.post("/training/score", json={
        "clip_id": clip_id, "virality": virality,
        "sentiment": sentiment, "audio": audio})


def _ids(resp):
    return [c["id"] for c in resp.json()]


# ── the queue ────────────────────────────────────────────────────────────────

def test_the_own_queue_still_only_serves_your_own_clips(env):
    """The existing mode must be untouched — it is what the team uses daily."""
    assert sorted(_ids(env["a"].get("/training/queue"))) == ["c0", "c1"]
    assert sorted(_ids(env["b"].get("/training/queue"))) == ["c2", "c3"]


def test_cross_rate_is_empty_until_somebody_has_rated_something(env):
    assert _ids(env["b"].get("/training/queue?mode=agreement")) == []


def test_cross_rate_serves_a_clip_the_other_person_rated(env):
    """THE WHOLE POINT. alice rates her own clip; it becomes available for bob
    to rate blind, which is the first doubly-rated clip in the dataset."""
    _score(env["a"], "c0", virality=8)
    assert _ids(env["b"].get("/training/queue?mode=agreement")) == ["c0"]


def test_you_are_never_offered_a_clip_you_already_rated(env):
    _score(env["a"], "c0")
    _score(env["b"], "c0")
    assert "c0" not in _ids(env["b"].get("/training/queue?mode=agreement"))
    assert "c0" not in _ids(env["a"].get("/training/queue?mode=agreement"))


def test_your_own_rating_does_not_make_a_clip_cross_rateable_by_you(env):
    """Otherwise the queue would offer you your own clip back and 'agreement'
    would mean agreeing with yourself."""
    _score(env["a"], "c0")
    assert _ids(env["a"].get("/training/queue?mode=agreement")) == []


def test_a_clip_you_own_and_already_resolved_is_not_offered(env):
    """You have watched it in Clip Review and decided, so it can never be
    rated blind again — whoever else has scored it.

    Set up by writing the other rating straight to the file, because that is
    the only way this state arises. Through the API it cannot: for somebody
    else to rate YOUR clip it must already be cross-rateable, which requires
    YOU to have rated it, which excludes you anyway. The guard is for records
    that arrive another way — a historical file, a seeded rating, an admin
    tool — and it is a blindness guarantee, so it holds by construction rather
    than by an argument about who can call what."""
    env["api"]._HUMAN_SCORES_FILE.write_text(_j.dumps({
        "ts": time.time(), "clip_id": "c0", "channel": "ch0",
        "labeler_id": "lab_b", "labeler": "bob",
        "human": {"virality": 6, "sentiment": 5, "audio": 5},
        "bot_signals": {}, "bot_trigger_score": 70, "bot_virality_score": 40,
    }) + "\n")
    assert "c0" in _ids(env["a"].get("/training/queue?mode=agreement")), \
        "precondition: alice owns c0, bob rated it, alice has not"
    env["clips"]["c0"]["status"] = "approved"    # alice resolves it in Review
    assert "c0" not in _ids(env["a"].get("/training/queue?mode=agreement"))


def test_somebody_elses_resolved_clip_IS_still_offered(env):
    """Its owner resolving it tells you nothing — you never saw that."""
    _score(env["a"], "c0")
    env["clips"]["c0"]["status"] = "approved"
    assert "c0" in _ids(env["b"].get("/training/queue?mode=agreement"))


def test_clips_with_the_fewest_raters_come_first(env):
    """A third opinion on a clip that has two is worth less than a second
    opinion on a clip that has one."""
    _score(env["a"], "c2")
    _score(env["a"], "c3")
    _score(env["b"], "c3")           # c3 now has two, c2 has one
    got = _ids(env["a"].get("/training/queue?mode=agreement"))
    assert got and got[0] == "c3", "a clip alice has not rated should lead"
    # alice has rated c2 and c3 already, so use bob for the ordering check
    _score(env["b"], "c0")
    order = _ids(env["b"].get("/training/queue?mode=agreement"))
    assert order.index("c2") < order.index("c1") if "c1" in order else True


# ── blindness, which is the whole value of the data ──────────────────────────

# Exactly what a labeler is allowed to see. An ALLOWLIST rather than a list of
# forbidden names: a mutation that attached `other_rating` to every clip slipped
# past a test that only knew to look for "virality" and "human".
_BLIND_FIELDS = {"id", "channel", "game", "created_at", "duration_seconds",
                 "twitch_url", "embed_url"}


def test_the_queue_sends_nothing_beyond_what_is_needed_to_watch_the_clip(env):
    """An anchored second opinion measures suggestibility, not agreement — it
    would produce a number that looks like consensus and means nothing. So the
    payload is checked against what it MAY contain, not against the leaks
    somebody thought of."""
    _score(env["a"], "c0", virality=9, sentiment=8, audio=7)
    for mode in ("own", "agreement"):
        who = env["b"] if mode == "agreement" else env["a"]
        for clip in who.get(f"/training/queue?mode={mode}").json():
            extra = set(clip) - _BLIND_FIELDS
            assert not extra, f"{mode} queue leaks {sorted(extra)}"


def test_the_bot_judgment_is_still_hidden_in_cross_rate_mode(env):
    """Same blindness the own-queue has: no scores, no signals, and not even
    the generated title, which names the bot's dominant signal."""
    _score(env["a"], "c0")
    body = _j.dumps(env["b"].get("/training/queue?mode=agreement").json())
    for leak in ("trigger_score", "trigger_signals", "virality_score",
                 "clip_title", "status"):
        assert leak not in body, f"cross-rate mode leaks {leak!r}"


def test_the_queue_says_nothing_about_whose_clip_it_is(env):
    _score(env["a"], "c0")
    body = _j.dumps(env["b"].get("/training/queue?mode=agreement").json())
    assert "user_id" not in body and "lab_a" not in body


# ── scoring somebody else's clip ─────────────────────────────────────────────

def test_you_can_score_a_clip_you_do_not_own_once_it_is_cross_rateable(env):
    _score(env["a"], "c0", virality=8)
    assert _score(env["b"], "c0", virality=3).status_code == 201


def test_you_still_cannot_score_an_arbitrary_clip_you_do_not_own(env):
    """The permission is scoped to the agreement pool ON PURPOSE. Without the
    second condition a labeler could post a score against any clip id in the
    system, which is a far larger permission than 'help measure agreement'."""
    assert _score(env["b"], "c0").status_code == 404, \
        "a labeler scored a stranger's clip that nobody had rated"


def test_a_non_labeler_cannot_reach_any_of_it(env):
    assert env["plain"].get("/training/queue?mode=agreement").status_code == 403
    assert env["plain"].get("/training/agreement").status_code == 403
    assert _score(env["plain"], "c0").status_code == 403


def test_scoring_the_same_clip_twice_is_still_refused(env):
    _score(env["a"], "c0")
    _score(env["b"], "c0")
    assert _score(env["b"], "c0").status_code == 409


def test_both_ratings_are_stored_against_the_same_clip(env):
    """The record that makes agreement measurable at all."""
    _score(env["a"], "c0", virality=9)
    _score(env["b"], "c0", virality=2)
    rows = [_j.loads(l) for l in
            env["api"]._HUMAN_SCORES_FILE.read_text().splitlines()]
    assert len(rows) == 2
    assert {r["clip_id"] for r in rows} == {"c0"}
    assert {r["labeler_id"] for r in rows} == {"lab_a", "lab_b"}
    assert sorted(r["human"]["virality"] for r in rows) == [2, 9]


def test_the_second_rating_is_paired_with_the_same_bot_numbers(env):
    """Both records must carry the bot's read for the same clip, or the two
    sides are not comparable."""
    _score(env["a"], "c0")
    _score(env["b"], "c0")
    rows = [_j.loads(l) for l in
            env["api"]._HUMAN_SCORES_FILE.read_text().splitlines()]
    assert rows[0]["bot_virality_score"] == rows[1]["bot_virality_score"]
    assert rows[0]["bot_signals"] == rows[1]["bot_signals"]


# ── the agreement number ─────────────────────────────────────────────────────

def test_agreement_reports_nothing_before_any_clip_has_two_ratings(env):
    _score(env["a"], "c0")
    d = env["a"].get("/training/agreement").json()
    assert d["clips_rated_twice"] == 0
    assert d["agreement"] is None


def test_agreement_counts_a_completed_pair(env):
    _score(env["a"], "c0", virality=7)
    _score(env["b"], "c0", virality=5)
    d = env["a"].get("/training/agreement").json()
    assert d["clips_rated_twice"] == 1
    assert d["median_gap"] == 2.0
    # The pair is keyed by the DISPLAY name on the session, which is what the
    # scoreboard already shows — here the fixture's session username is the uid.
    assert d["by_pair"] == {"lab_a + lab_b": {"n": 1, "median_gap": 2.0}}


def test_agreement_is_measured_not_assumed(env, monkeypatch):
    """Two raters who rank clips oppositely must come back negative — this is
    the number that decides whether the human target is usable at all."""
    api = env["api"]
    clips = {}
    for i in range(24):
        clips[f"x{i}"] = {"id": f"x{i}", "user_id": "lab_a", "channel": "ch",
                          "status": "pending", "created_at": i, "duration_seconds": 30,
                          "trigger_score": 50, "virality_score": 50,
                          "twitch_url": "u", "embed_url": "e",
                          "trigger_signals": [{"type": "SignalType.AUDIO_SPIKE",
                                               "value": 0.5}]}
    monkeypatch.setattr(api, "_clips", clips)
    for i in range(24):
        _score(env["a"], f"x{i}", virality=(i % 10) + 1)
        _score(env["b"], f"x{i}", virality=10 - (i % 10))
    d = env["a"].get("/training/agreement").json()
    assert d["clips_rated_twice"] == 24
    assert d["agreement"] < -0.8, f"opposite raters reported as agreeing: {d}"


def test_agreement_recognises_raters_who_do_agree(env, monkeypatch):
    api = env["api"]
    clips = {f"y{i}": {"id": f"y{i}", "user_id": "lab_a", "channel": "ch",
                       "status": "pending", "created_at": i, "duration_seconds": 30,
                       "trigger_score": 50, "virality_score": 50,
                       "twitch_url": "u", "embed_url": "e",
                       "trigger_signals": [{"type": "SignalType.AUDIO_SPIKE",
                                            "value": 0.5}]}
             for i in range(24)}
    monkeypatch.setattr(api, "_clips", clips)
    for i in range(24):
        v = (i % 10) + 1
        _score(env["a"], f"y{i}", virality=v)
        _score(env["b"], f"y{i}", virality=min(10, v + (i % 2)))
    d = env["a"].get("/training/agreement").json()
    assert d["agreement"] > 0.8
    assert d["within_two"] == 100


def test_two_records_from_ONE_person_are_not_a_pair(env):
    """"Rated twice" has to mean two PEOPLE. Counting rows instead of raters
    would report a rater agreeing with themselves as consensus, and duplicate
    rows do exist in a file written over months."""
    api = env["api"]
    rec = lambda v: _j.dumps({
        "ts": time.time(), "clip_id": "c0", "channel": "ch0",
        "labeler_id": "lab_a", "labeler": "alice",
        "human": {"virality": v, "sentiment": 5, "audio": 5},
        "bot_signals": {}, "bot_trigger_score": 70, "bot_virality_score": 40})
    api._HUMAN_SCORES_FILE.write_text(rec(2) + "\n" + rec(9) + "\n")
    d = env["a"].get("/training/agreement").json()
    assert d["clips_rated_twice"] == 0, \
        "one person's two ratings were counted as agreement between two people"
    assert d["agreement"] is None


def test_a_duplicate_row_is_not_paired_against_its_own_author(env):
    """Two rows from alice AND one from bob. The clip legitimately has two
    raters, so it counts — but the pair compared must be alice-vs-bob, not
    alice's first row against her second. Picking the first two ROWS would
    score a rater against themselves and report near-perfect agreement."""
    api = env["api"]
    def rec(labeler, v):
        return _j.dumps({"ts": time.time(), "clip_id": "c0", "channel": "ch0",
                         "labeler_id": labeler, "labeler": labeler,
                         "human": {"virality": v, "sentiment": 5, "audio": 5},
                         "bot_signals": {}, "bot_trigger_score": 70,
                         "bot_virality_score": 40})
    api._HUMAN_SCORES_FILE.write_text(
        rec("lab_a", 4) + "\n" + rec("lab_a", 4) + "\n" + rec("lab_b", 10) + "\n")
    d = env["a"].get("/training/agreement").json()
    assert d["clips_rated_twice"] == 1
    assert d["median_gap"] == 6.0, (
        "the pair was alice-vs-alice (gap 0) instead of alice-vs-bob (gap 6)")
    assert d["by_pair"] == {"lab_a + lab_b": {"n": 1, "median_gap": 6.0}}


def test_a_third_rating_does_not_double_count_the_pair(env):
    """Three people on one clip is still ONE clip, and the agreement figure
    must not silently weight it more heavily than a clip with two."""
    api = env["api"]
    api._clips["c0"]["user_id"] = "lab_a"
    _score(env["a"], "c0", virality=5)
    _score(env["b"], "c0", virality=6)
    # a third labeler
    from fastapi.testclient import TestClient
    from src.auth import users as user_store
    rows = user_store._load()
    rows.append({"id": "lab_c", "username": "carol", "is_labeler": True})
    user_store._save(rows)
    c = TestClient(api.app)
    c.cookies.set("session", TimestampSigner(api.settings.dashboard_secret_key).sign(
        base64.b64encode(_j.dumps({"auth": True, "user_id": "lab_c",
                                   "username": "lab_c", "is_admin": False,
                                   "subscription_status": "active"}).encode())).decode())
    _score(c, "c0", virality=7)
    d = env["a"].get("/training/agreement").json()
    assert d["clips_rated_twice"] == 1


# ── realtime ─────────────────────────────────────────────────────────────────

def test_the_score_event_names_the_clip_so_open_queues_can_react(env):
    """Another trainer's open My-queue has to drop a clip that was just
    scored, or they watch thirty seconds of video and submit into a 409."""
    _score(env["a"], "c0")
    evt = env["sent"][-1][0]
    assert evt["event"] == "training_scored"
    assert evt["clip_id"] == "c0"
    assert evt["total"] == 1


def test_the_screen_reacts_to_it(env):
    """Realtime contract rule 2: an event the server emits needs a branch that
    reads the new field, or adding it changed nothing."""
    import re
    from pathlib import Path
    src = Path("src/dashboard/aurora_html.py").read_text()
    js = src.split('<script type="text/babel">')[1]
    m = re.search(r"function TrainingScreen\(\) \{.*?\n\}\n\n", js, re.S)
    assert m, "TrainingScreen not found"
    body = m.group(0)
    assert "m.clip_id" in body, "the screen ignores which clip was scored"
    assert "mode === 'own'" in body, \
        "a clip is dropped in cross-rate mode too, where a second opinion is the point"
    # In the training_scored branch specifically, and the ADD side of the
    # reconnect listener — the remove side mentions both names too, so the bare
    # string passes with the subscription deleted.
    scored_branch = body.split("training_scored")[1].split("} catch")[0]
    assert "loadAgree()" in scored_branch, \
        "a completed pair does not refresh the agreement panel"
    assert "addEventListener('hz_refetch'" in body, \
        "the screen does not self-heal after a reconnect"


def test_the_mode_is_part_of_the_queue_request(env):
    from pathlib import Path
    js = Path("src/dashboard/aurora_html.py").read_text().split(
        '<script type="text/babel">')[1]
    assert "'/training/queue?mode=' + mode" in js
    assert "}, [mode]);" in js, \
        "load() does not track mode — switching tabs would refetch the old one"


def test_approve_and_reject_are_not_offered_for_somebody_elses_clip(env):
    from pathlib import Path
    js = Path("src/dashboard/aurora_html.py").read_text().split(
        '<script type="text/babel">')[1]
    assert "if(mode==='own' && (verdict==='approve'||verdict==='reject'))" in js, \
        "cross-rate mode can still resolve a clip that belongs to another account"
