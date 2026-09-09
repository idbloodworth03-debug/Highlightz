"""
The signup funnel counters.

WHAT IS ACTUALLY AT RISK HERE. This feature exists to answer one question —
"is nobody arriving, or are they arriving and leaving?" — and the way it fails
is not by crashing. It fails by producing a plausible number that is wrong, and
then somebody spends a month promoting into the wrong end of the problem.

So these tests are mostly about the arithmetic and the double-counting:

  * the "first ever" steps must count once per account, or the funnel converts
    at several hundred percent and reads as healthy;
  * a signed-in user reloading their dashboard is not a new visitor;
  * returning sign-ins must stay OUT of the conversion maths;
  * a pageview must not be a disk write, on a box that cannot afford it;
  * and nothing here may ever raise into a request — an analytics bug taking
    down the landing page is a far worse outcome than a lost count.
"""

import json

import pytest

from src.dashboard import funnel


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    """Point the counter at a scratch file; never the real store."""
    monkeypatch.setattr(funnel, "_PATH", tmp_path / "funnel.json")
    funnel._counts.clear()
    funnel._once.clear()
    funnel._loaded = True
    funnel._dirty = False
    funnel._last_flush = 0.0
    yield tmp_path
    funnel._counts.clear()
    funnel._once.clear()


# ── counting ─────────────────────────────────────────────────────────────────

def test_a_step_is_counted_against_today():
    funnel.record("landing")
    funnel.record("landing")
    assert funnel.totals()["totals"]["landing"] == 2


def test_an_unknown_step_is_dropped_rather_than_stored():
    """The admin page renders from STEPS, so a typo'd key would be counted
    forever and displayed nowhere — a number that exists and cannot be seen."""
    funnel.record("landng")                    # typo
    assert "landng" not in funnel.totals()["totals"]
    assert sum(funnel.totals()["totals"].values()) == 0


def test_recording_never_raises_into_the_request(monkeypatch):
    """An analytics bug must never be able to take down the landing page."""
    def boom(*a, **k):
        raise RuntimeError("disk gone")
    monkeypatch.setattr(funnel, "_today", boom)
    funnel.record("landing")                   # must not raise
    monkeypatch.setattr(funnel, "flush", boom)
    funnel.record_once("first_clip", "u1")     # must not raise either


# ── the "first ever" steps ───────────────────────────────────────────────────

def test_a_milestone_counts_once_per_account():
    """THE BUG THIS PREVENTS. Without the marker, every clip a user receives
    counts as 'got their first clip'. A user with 800 clips would then make the
    funnel report an 800-clip conversion from a single signup, and the whole
    panel would read as healthy while nobody was signing up at all."""
    for _ in range(50):
        funnel.record_once("first_clip", "u1")
    assert funnel.totals()["totals"]["first_clip"] == 1


def test_each_account_gets_its_own_milestone():
    funnel.record_once("first_channel", "u1")
    funnel.record_once("first_channel", "u2")
    assert funnel.totals()["totals"]["first_channel"] == 2


def test_a_milestone_says_whether_it_counted():
    assert funnel.record_once("first_clip", "u1") is True
    assert funnel.record_once("first_clip", "u1") is False


def test_a_milestone_without_an_account_is_ignored():
    """clip_uid can be missing on a malformed record. Counting that would
    credit a milestone to nobody and inflate the last step."""
    assert funnel.record_once("first_clip", "") is False
    assert funnel.totals()["totals"]["first_clip"] == 0


def test_a_marked_but_uncounted_account_does_not_count_later():
    """How staff are excluded: the account is remembered so the caller's
    "is this person staff?" lookup runs once rather than on every clip, but it
    never reaches the total."""
    assert funnel.record_once("first_clip", "staff", count=False) is False
    assert funnel.totals()["totals"]["first_clip"] == 0
    # Already seen, so a later clip does not sneak it in.
    assert funnel.record_once("first_clip", "staff") is False
    assert funnel.totals()["totals"]["first_clip"] == 0


def test_excluding_one_account_does_not_affect_another():
    funnel.record_once("first_clip", "staff", count=False)
    funnel.record_once("first_clip", "real")
    assert funnel.totals()["totals"]["first_clip"] == 1


def test_an_exclusion_survives_a_restart(scratch):
    """Otherwise the next deploy counts the owner's account on its next clip,
    which is exactly the number this is meant to keep out."""
    funnel.record_once("first_clip", "staff", count=False)
    funnel.flush(force=True)
    funnel._loaded = False
    funnel.load()
    assert funnel.record_once("first_clip", "staff") is False
    assert funnel.totals()["totals"]["first_clip"] == 0


def test_milestones_survive_a_restart(scratch):
    """They live in the same file as the counts. If they did not persist, a
    deploy would re-credit every existing user on their next clip."""
    funnel.record_once("first_clip", "u1")
    funnel.flush(force=True)
    funnel._loaded = False
    funnel.load()
    assert funnel.record_once("first_clip", "u1") is False
    assert funnel.totals()["totals"]["first_clip"] == 1


# ── the arithmetic the admin page renders ────────────────────────────────────

def test_step_to_step_conversion_is_against_the_previous_step():
    funnel.record("landing", 100)
    funnel.record("login_view", 50)
    funnel.record("oauth_start", 25)
    rows = {r["key"]: r for r in funnel.totals()["rows"]}
    assert rows["login_view"]["pct_prev"] == 50.0     # 50 of 100
    assert rows["oauth_start"]["pct_prev"] == 50.0    # 25 of 50
    assert rows["oauth_start"]["pct_top"] == 25.0     # 25 of 100


def test_returning_signins_are_kept_out_of_the_conversion_maths():
    """A returning sign-in is not a funnel stage. Counted as one, it would make
    every step after it look like it leaked people who in fact completed the
    funnel months ago."""
    funnel.record("oauth_return", 10)
    funnel.record("returning", 8)
    funnel.record("signup", 2)
    out = funnel.totals()
    assert "returning" not in {r["key"] for r in out["rows"]}
    # signup is measured against oauth_return, not against `returning`.
    rows = {r["key"]: r for r in out["rows"]}
    assert rows["signup"]["pct_prev"] == 20.0
    # ...but the raw number is still reported, so the page can show it.
    assert out["totals"]["returning"] == 8


def test_the_first_step_has_no_previous_to_convert_from():
    funnel.record("landing", 10)
    first = funnel.totals()["rows"][0]
    assert first["key"] == "landing"
    assert first["pct_prev"] is None


def test_an_empty_funnel_does_not_divide_by_zero():
    out = funnel.totals()
    assert all(r["count"] == 0 for r in out["rows"])
    assert all(r["pct_prev"] is None or r["pct_prev"] == 0 for r in out["rows"])


def test_a_step_with_no_traffic_above_it_does_not_report_a_percentage():
    """Guards the divide when an intermediate step is zero — otherwise the
    step below it raises rather than rendering."""
    funnel.record("landing", 5)
    funnel.record("signup", 1)          # nothing counted in between
    rows = {r["key"]: r for r in funnel.totals()["rows"]}
    assert rows["login_view"]["pct_prev"] == 0.0
    assert rows["signup"]["pct_prev"] is None


# ── persistence and cost ─────────────────────────────────────────────────────

def test_a_pageview_is_not_a_disk_write(scratch):
    """The box is a 1 vCPU droplet already running an ffmpeg per channel.
    Counts are held in memory and flushed on a timer."""
    funnel._last_flush = 9e18            # pretend we just flushed
    funnel.record("landing")
    assert not (scratch / "funnel.json").exists()
    funnel._last_flush = 0.0
    funnel.flush(force=True)
    assert (scratch / "funnel.json").exists()


def test_reading_the_totals_flushes_first(scratch):
    """So the admin page can never show numbers staler than the disk."""
    funnel._last_flush = 9e18
    funnel.record("landing")
    funnel.totals()
    assert (scratch / "funnel.json").exists()


def test_counts_survive_a_restart(scratch):
    funnel.record("landing", 7)
    funnel.flush(force=True)
    funnel._loaded = False
    funnel.load()
    assert funnel.totals()["totals"]["landing"] == 7


def test_a_corrupt_file_does_not_stop_the_app_booting(scratch):
    """Losing the history is annoying. Failing to start is an outage."""
    (scratch / "funnel.json").write_text("{ this is not json")
    funnel._loaded = False
    funnel.load()                        # must not raise
    assert funnel.totals()["totals"]["landing"] == 0


def test_an_old_format_file_is_ignored_rather_than_crashing(scratch):
    """The file gained a wrapper when milestones were added. A file written by
    the previous shape must not take the process down on the next deploy."""
    (scratch / "funnel.json").write_text(json.dumps({"2026-09-09": {"landing": 3}}))
    funnel._loaded = False
    funnel.load()                        # must not raise
    assert funnel.totals()["days"] >= 0


def test_the_file_cannot_grow_without_limit(scratch):
    """A counter with no ceiling is one more thing that silently fills a disk
    already shared with the clip store and the capture buffer."""
    for i in range(funnel._KEEP_DAYS + 40):
        funnel._counts[f"2020-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}-{i}"] = {"landing": 1}
    funnel._dirty = True
    funnel.flush(force=True)
    assert len(funnel._counts) <= funnel._KEEP_DAYS


# ── what the page needs ──────────────────────────────────────────────────────

def test_every_step_carries_a_label_and_an_explanation():
    """The panel renders these directly. A step with no words is a bare number
    on a screen whose whole job is telling somebody what to do next."""
    out = funnel.totals()
    assert len(out["steps"]) == len(funnel.STEPS)
    for s in out["steps"]:
        assert s["label"] and s["help"]


def test_the_window_only_covers_the_days_asked_for():
    funnel._counts["2020-01-01"] = {"landing": 999}
    funnel.record("landing", 3)
    assert funnel.totals(days=1)["totals"]["landing"] == 3


# ── the wiring, through the real routes ──────────────────────────────────────
#
# The unit tests above prove the arithmetic. These prove the counters are
# attached to the right BRANCH, which is the half that silently goes wrong:
# counting a logged-in dashboard load as a new visitor, or counting a clip on
# the path that throws it away, produces numbers that look fine and are not.

import base64
import json as _j

from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from src.dashboard import api


@pytest.fixture
def client(scratch, monkeypatch):
    monkeypatch.setattr(api, "funnel", funnel)
    c = TestClient(api.app)

    def login(uid="u1"):
        signer = TimestampSigner(api.settings.dashboard_secret_key)
        data = base64.b64encode(_j.dumps({
            "auth": True, "user_id": uid, "subscription_status": "active",
        }).encode())
        c.cookies.set("session", signer.sign(data).decode())
        return c

    c.login = login
    return c


def test_a_signed_out_visit_to_the_front_page_counts_as_a_landing(client):
    client.get("/")
    assert funnel.totals()["totals"]["landing"] == 1


def test_a_signed_in_dashboard_load_is_not_a_landing(client):
    """THE BUG THIS PREVENTS. The dashboard is a long-lived tab that people
    keep open for hours and reload often. Counted as landings, an active user
    would flood the top of the funnel and make conversion fall as the product
    got MORE used — exactly backwards."""
    from src.auth import users as user_store
    monkey = {"id": "u1", "subscription_status": "active", "plan": "pro"}
    orig = user_store.get_by_id
    user_store.get_by_id = lambda uid: monkey if uid == "u1" else orig(uid)
    try:
        client.login("u1").get("/")
    finally:
        user_store.get_by_id = orig
    assert funnel.totals()["totals"]["landing"] == 0


def test_the_sign_in_page_is_counted(client):
    client.get("/login")
    assert funnel.totals()["totals"]["login_view"] == 1


def test_an_anonymous_caller_never_sees_the_funnel(client):
    """Gated by AuthMiddleware BEFORE the route runs, so the refusal is a
    redirect to /login rather than a 403 — asserted as behaviour (no data
    comes back) rather than as a status code, because the mechanism is the
    middleware's to choose and the guarantee is what matters."""
    r = client.get("/admin/funnel")
    assert "landing" not in r.text
    assert r.status_code != 200 or "totals" not in r.text


def test_a_signed_in_non_admin_never_sees_the_funnel(client, monkeypatch):
    """Past the middleware, _require_admin is what stops them. Checked against
    the database rather than the session, so forging is_admin in a cookie is
    not enough."""
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "is_admin": False,
                                     "subscription_status": "active", "plan": "pro"})
    r = client.login("u1").get("/admin/funnel")
    assert r.status_code == 403


def test_the_window_parameter_cannot_be_absurd(client, monkeypatch):
    """It reaches a list slice from a query string. Clamped, not trusted."""
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "is_admin": True,
                                     "subscription_status": "active", "plan": "pro"})
    c = client.login("admin1")
    funnel.record("landing", 3)
    for bad in ("-5", "0", "99999"):
        r = c.get(f"/admin/funnel?days={bad}")
        assert r.status_code == 200, r.text
        assert r.json()["days"] >= 0


def test_an_admin_gets_the_counts(client, monkeypatch):
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "is_admin": True,
                                     "subscription_status": "active", "plan": "pro"})
    funnel.record("landing", 4)
    funnel.record("signup", 1)
    body = client.login("admin1").get("/admin/funnel").json()
    assert body["totals"]["landing"] == 4
    assert {r["key"] for r in body["rows"]} >= {"landing", "signup"}


# ── staff exclusion, through the real clip path ──────────────────────────────

@pytest.mark.asyncio
async def test_an_admins_clip_does_not_count_as_an_activated_user(client, monkeypatch):
    """THE NUMBER THIS PROTECTS. The owner's account holds hundreds of clips
    from testing on live channels. Counted, it reports the product as having
    activated a user it never acquired — and on a funnel whose totals are in
    single digits that is the difference between "nothing works" and
    "activation looks fine"."""
    from src.auth import users as user_store
    people = {"boss":  {"id": "boss", "is_admin": True,
                        "subscription_status": "active", "plan": "pro"},
              "punter": {"id": "punter", "is_admin": False,
                         "subscription_status": "active", "plan": "pro"}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "increment_clip_counter", lambda *a, **k: None)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)
    api._clips.clear()
    try:
        await api.notify_clip_ready({
            "id": "c1", "user_id": "boss", "channel": "lacy",
            "status": "pending", "created_at": 1000.0, "platform": "twitch"})
        assert funnel.totals()["totals"]["first_clip"] == 0

        await api.notify_clip_ready({
            "id": "c2", "user_id": "punter", "channel": "marlon",
            "status": "pending", "created_at": 2000.0, "platform": "twitch"})
        assert funnel.totals()["totals"]["first_clip"] == 1
    finally:
        api._clips.clear()


@pytest.mark.asyncio
async def test_the_admin_lookup_happens_once_not_per_clip(client, monkeypatch):
    """The exclusion marks the account as seen. Without that mark the store
    would be re-read for every clip the owner's account ever receives, which on
    an account with hundreds of them is a full user-file read each time."""
    from src.auth import users as user_store
    calls = {"n": 0}

    def counting_get(uid):
        calls["n"] += 1
        return {"id": "boss", "is_admin": True,
                "subscription_status": "active", "plan": "pro"}
    monkeypatch.setattr(user_store, "get_by_id", counting_get)
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "increment_clip_counter", lambda *a, **k: None)

    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(api, "broadcast", _noop)
    api._clips.clear()
    try:
        for i in range(5):
            await api.notify_clip_ready({
                "id": f"x{i}", "user_id": "boss", "channel": f"ch{i}",
                "status": "pending", "created_at": 1000.0 + i * 999,
                "platform": "twitch"})
        assert funnel.totals()["totals"]["first_clip"] == 0
        # The account is remembered, so it is never re-counted...
        assert funnel.record_once("first_clip", "boss") is False
    finally:
        api._clips.clear()


# ── staff leave no trace anywhere ────────────────────────────────────────────
#
# The first four steps happen before anybody has an identity: a landing page
# view has no account attached to it. The only way to keep staff out of them is
# to write down what a browsing session was counted for and subtract it again
# when that session turns out to belong to staff. These pin that reversal, and
# especially the ways it could be abused or go negative.

def test_a_journalled_step_is_counted_normally():
    j = []
    funnel.record("landing", journal=j)
    assert funnel.totals()["totals"]["landing"] == 1
    assert j and j[0][1] == "landing"


def test_undo_takes_back_exactly_what_the_session_was_counted_for():
    j = []
    for step in ("landing", "login_view", "oauth_start", "oauth_return"):
        funnel.record(step, journal=j)
    funnel.record("landing")                 # somebody else, not in the journal
    assert funnel.undo(j) == 4
    t = funnel.totals()["totals"]
    assert t["landing"] == 1                 # the stranger's visit survives
    assert t["login_view"] == 0 and t["oauth_start"] == 0 and t["oauth_return"] == 0


def test_undo_reverses_into_the_day_the_step_was_counted():
    """A staff sign-in on Friday must not decrement Monday's bucket, or the
    daily series goes wrong in both directions at once."""
    funnel._counts["2026-09-01"] = {"landing": 5}
    assert funnel.undo([["2026-09-01", "landing"]]) == 1
    assert funnel._counts["2026-09-01"]["landing"] == 4


def test_undo_can_never_drive_a_count_negative():
    """The journal rides a session cookie, which is a thing the CLIENT holds.
    A replayed or edited cookie must not be able to make the funnel report a
    step that nobody reached."""
    j = [[funnel._today(), "landing"]] * 50
    funnel.record("landing")
    funnel.undo(j)
    funnel.undo(j)
    assert funnel.totals()["totals"]["landing"] == 0


def test_undo_ignores_junk_in_the_journal():
    """Same reason: the shape arrives from a cookie, not from us."""
    funnel.record("landing")
    funnel.undo([None, [], ["nope"], ["2026-01-01", "not_a_step"],
                 ["1999-01-01", "landing"], "garbage"])
    assert funnel.totals()["totals"]["landing"] == 1


def test_undo_on_an_empty_journal_is_harmless():
    assert funnel.undo(None) == 0
    assert funnel.undo([]) == 0


def test_the_journal_cannot_grow_without_bound():
    """It lives in a session cookie. Somebody reloading the sign-in page two
    hundred times must not produce a cookie too big to send."""
    j = []
    for _ in range(200):
        funnel.record("login_view", journal=j)
    assert len(j) <= funnel._JOURNAL_MAX


@pytest.mark.asyncio
async def test_an_admin_signing_in_leaves_the_funnel_untouched(client, monkeypatch):
    """END TO END, through the real routes. The owner is the most frequent
    visitor to their own site; on a funnel whose totals are in single digits,
    their sign-ins alone would dominate every step above the account level."""
    from src.auth import users as user_store
    admin = {"id": "boss", "is_admin": True, "username": "boss",
             "subscription_status": "active", "plan": "pro"}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: admin)
    monkeypatch.setattr(user_store, "get_by_twitch_id", lambda tid: admin)
    monkeypatch.setattr(user_store, "upsert_twitch_user", lambda **k: admin)
    monkeypatch.setattr(user_store, "mark_login", lambda uid: None)
    monkeypatch.setattr(user_store, "set_email", lambda *a, **k: None)

    async def _exchange(code):
        return {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

    async def _get_user(tok):
        return {"id": "42", "login": "boss", "username": "boss"}
    from src.auth import twitch_oauth
    monkeypatch.setattr(twitch_oauth, "exchange_code", _exchange)
    monkeypatch.setattr(twitch_oauth, "get_user", _get_user)
    monkeypatch.setattr(twitch_oauth, "authorization_url",
                        lambda state: "https://id.twitch.tv/oauth2/authorize")
    monkeypatch.setattr(api.settings, "twitch_client_id", "test-client")

    # base_url is https on purpose: dashboard_https_only marks the session
    # cookie Secure, and an http test client silently never sends it back —
    # which makes every request look like a brand-new session.
    c = TestClient(api.app, follow_redirects=False, base_url="https://testserver")
    c.get("/")                                  # landing
    c.get("/login")                             # sign-in page
    r = c.get("/auth/twitch")                   # off to Twitch
    assert r.status_code in (302, 307), r.status_code
    # Everything above is counted; nobody knows who they are yet.
    assert funnel.totals()["totals"]["landing"] == 1

    # The CSRF state rode out in the session cookie. Read it back and replay
    # the callback the way Twitch would.
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    raw = signer.unsign(c.cookies.get("session")).decode()
    state = _j.loads(base64.b64decode(raw)).get("oauth_state")
    assert state, "no oauth state was stored"

    c.get(f"/auth/twitch/callback?code=abc&state={state}")

    t = funnel.totals()["totals"]
    assert t["landing"] == 0, "the admin's landing view was not taken back"
    assert t["login_view"] == 0
    assert t["oauth_start"] == 0
    assert t["oauth_return"] == 0
    assert t["signup"] == 0 and t["returning"] == 0


def test_the_journal_accumulates_across_requests():
    """THE BUG THIS CAUGHT, and it failed invisibly.

    Starlette's Session rewrites the cookie only when `session.modified` is
    set, and that flag is set by DICT operations. Appending to a list already
    inside the session never touches the dict, so the cookie was never re-sent
    and the journal reset to a single entry on every request.

    Nothing looked wrong: every step was still counted correctly, the cookie
    still existed, and no error was raised anywhere. It would have surfaced
    only as a staff sign-in taking back one step instead of four — a wrong
    number on a panel built to be trusted, which is the worst shape of failure
    this feature has.
    """
    c = TestClient(api.app, follow_redirects=False, base_url="https://testserver")
    c.get("/")
    c.get("/login")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    sess = _j.loads(base64.b64decode(signer.unsign(c.cookies.get("session")).decode()))
    steps = [e[1] for e in sess.get("_fn", [])]
    assert steps == ["landing", "login_view"], steps


def test_why_these_tests_speak_https():
    """A note to the next person, written as a test so it cannot go stale.

    dashboard_https_only marks the session cookie Secure. httpx keeps such a
    cookie in its jar but will not SEND it to an http:// URL, so over http every
    request arrives with no session and the journal never accumulates — which
    would make the tests above pass against a product that was in fact broken.
    Hence base_url="https://testserver" everywhere the journal matters.
    """
    http = TestClient(api.app, follow_redirects=False, base_url="http://testserver")
    http.get("/")
    http.get("/login")
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    sess = _j.loads(base64.b64decode(signer.unsign(http.cookies.get("session")).decode()))
    # One entry, not two: the second request never saw the first one's session.
    assert [e[1] for e in sess.get("_fn", [])] == ["login_view"]
