"""The acceptance rate on the dashboard.

The number a streamer is shown for "how often does this thing pick clips I
actually want". It comes from the append-only stats ledger, NOT from the live
clip store, and every test here exists to hold that line: rejecting a clip
deletes it server-side, so anything counted off `_clips` has lost half the
fraction before it starts.
"""

import base64
import json as _j

import pytest
from itsdangerous import TimestampSigner

from src.stats import stream_stats as ss


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    """Every test gets its own ledger. Without this they append to the real
    one and to each other, and the counts drift upward as the file runs."""
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stream_stats.jsonl")
    yield tmp_path


@pytest.fixture
def client(log_file):
    from fastapi.testclient import TestClient
    from src.dashboard import api

    api._clips.clear()

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)

    def login(uid):
        c.cookies.clear()
        c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
            {"auth": True, "user_id": uid, "username": uid,
             "subscription_status": "active"}).encode())).decode())
        return c

    c.login = login
    c.api = api
    yield c
    api._clips.clear()


def rec(event, uid="u1", channel="novafps", cid="c1"):
    ss.record(event, {"id": cid, "user_id": uid, "channel": channel})


# ── the ledger side ──────────────────────────────────────────────────────────

def test_the_rate_counts_rejections_the_clip_store_has_thrown_away():
    """The whole point. A user who kept 1 of 4 has a 25% rate; counting live
    clips would see one approved clip, no rejections, and call it 100%."""
    for i in range(4):
        rec(ss.CAUGHT, cid=f"c{i}")
    rec(ss.APPROVED, cid="c0")
    for i in (1, 2, 3):
        rec(ss.REJECTED, cid=f"c{i}")
    t = ss.totals_for_user("u1")
    assert (t["approved"], t["rejected"], t["reviewed"]) == (1, 3, 4)
    assert t["kept_pct"] == 25


def test_a_clip_still_waiting_in_the_queue_is_not_a_rejection():
    """Denominator is what was judged. Counting the untouched queue would show
    someone who kept every clip they looked at a falling rate as clips arrive."""
    for i in range(10):
        rec(ss.CAUGHT, cid=f"c{i}")
    rec(ss.APPROVED, cid="c0")
    rec(ss.APPROVED, cid="c1")
    t = ss.totals_for_user("u1")
    assert t["caught"] == 10
    assert t["reviewed"] == 2
    assert t["kept_pct"] == 100


def test_clearing_the_queue_does_not_count_against_the_rate():
    """Clearing says 'I am not going to get to these', not 'these were bad'.
    Folding it into rejected would punish the formula for a busy week."""
    for i in range(6):
        rec(ss.CAUGHT, cid=f"c{i}")
    rec(ss.APPROVED, cid="c0")
    rec(ss.REJECTED, cid="c1")
    for i in range(2, 6):
        rec(ss.CLEARED, cid=f"c{i}")
    t = ss.totals_for_user("u1")
    assert t["cleared"] == 4
    assert t["reviewed"] == 2
    assert t["kept_pct"] == 50


def test_an_undone_rejection_stops_counting_against_the_rate():
    rec(ss.CAUGHT, cid="c1")
    rec(ss.CAUGHT, cid="c2")
    rec(ss.APPROVED, cid="c1")
    rec(ss.REJECTED, cid="c2")
    assert ss.totals_for_user("u1")["kept_pct"] == 50
    rec(ss.UNDONE, cid="c2")
    t = ss.totals_for_user("u1")
    assert t["rejected"] == 0
    assert t["kept_pct"] == 100, "the retracted reject still dragged the rate down"


def test_one_users_rate_never_sees_another_users_clips():
    rec(ss.CAUGHT, uid="u1", cid="c1")
    rec(ss.APPROVED, uid="u1", cid="c1")
    rec(ss.CAUGHT, uid="u2", cid="c2")
    rec(ss.REJECTED, uid="u2", cid="c2")
    assert ss.totals_for_user("u1")["kept_pct"] == 100
    assert ss.totals_for_user("u2")["kept_pct"] == 0


def test_the_rate_spans_every_channel_the_user_watches():
    rec(ss.CAUGHT, channel="a", cid="c1")
    rec(ss.APPROVED, channel="a", cid="c1")
    rec(ss.CAUGHT, channel="b", cid="c2")
    rec(ss.REJECTED, channel="b", cid="c2")
    t = ss.totals_for_user("u1")
    assert (t["approved"], t["rejected"]) == (1, 1)
    assert t["kept_pct"] == 50


def test_an_undo_on_one_channel_cannot_retract_another_channels_reject():
    """The retraction set is built per channel; built across them, a clip id
    reused on a second channel would cancel the wrong row."""
    for ch in ("a", "b"):
        rec(ss.CAUGHT, channel=ch, cid="dup")
        rec(ss.REJECTED, channel=ch, cid="dup")
    rec(ss.UNDONE, channel="a", cid="dup")
    assert ss.totals_for_user("u1")["rejected"] == 1


def test_a_user_who_has_judged_nothing_has_no_rate_rather_than_a_bad_one():
    for i in range(3):
        rec(ss.CAUGHT, cid=f"c{i}")
    t = ss.totals_for_user("u1")
    assert t["reviewed"] == 0
    assert t["kept_pct"] == 0, "no rate is expressed as 0 reviewed, not a rate"


def test_an_empty_ledger_is_zeroes_not_a_crash():
    t = ss.totals_for_user("nobody")
    assert t["reviewed"] == 0 and t["kept_pct"] == 0
    assert t["caught"] == 0


def test_the_users_own_rate_matches_what_the_admin_table_shows_them():
    """Two screens, one account, one number. They are computed by the same
    helper so they cannot drift — this is the test that keeps it that way."""
    for i in range(8):
        rec(ss.CAUGHT, channel="a", cid=f"c{i}")
    for i in range(5):
        rec(ss.APPROVED, channel="a", cid=f"c{i}")
    for i in range(5, 7):
        rec(ss.REJECTED, channel="a", cid=f"c{i}")
    rec(ss.CLEARED, channel="a", cid="c7")
    mine = ss.totals_for_user("u1")
    admin = ss.totals_by_user()["u1"]
    assert mine == admin


# ── the endpoint ─────────────────────────────────────────────────────────────

def test_the_endpoint_reports_the_ledgers_rate(client):
    for i in range(5):
        rec(ss.CAUGHT, uid="u1", cid=f"c{i}")
    for i in range(3):
        rec(ss.APPROVED, uid="u1", cid=f"c{i}")
    for i in (3, 4):
        rec(ss.REJECTED, uid="u1", cid=f"c{i}")
    d = client.login("u1").get("/stats/acceptance").json()
    assert d["rate"] == 60
    assert (d["approved"], d["rejected"], d["reviewed"]) == (3, 2, 5)
    assert d["caught"] == 5


def test_the_endpoint_shows_you_only_your_own_rate(client):
    rec(ss.CAUGHT, uid="u1", cid="c1")
    rec(ss.APPROVED, uid="u1", cid="c1")
    rec(ss.CAUGHT, uid="u2", cid="c2")
    rec(ss.REJECTED, uid="u2", cid="c2")
    assert client.login("u1").get("/stats/acceptance").json()["rate"] == 100
    assert client.login("u2").get("/stats/acceptance").json()["rate"] == 0


def test_the_endpoint_disagrees_with_the_per_channel_approval_rate_on_purpose(client):
    """/stats' `approval_rate` is approved / what is LEFT in the clip store, so
    it cannot see rejections and reads 100% here. If this ever starts agreeing,
    someone has wired the dashboard tile to the wrong source."""
    for i in range(4):
        rec(ss.CAUGHT, uid="u1", cid=f"c{i}")
    rec(ss.APPROVED, uid="u1", cid="c0")
    for i in (1, 2, 3):
        rec(ss.REJECTED, uid="u1", cid=f"c{i}")
    client.api._clips["c0"] = {"id": "c0", "user_id": "u1", "channel": "novafps",
                               "status": "approved", "created_at": 1000.0}
    c = client.login("u1")
    assert c.get("/stats").json()[0]["approval_rate"] == 100.0
    assert c.get("/stats/acceptance").json()["rate"] == 25


def test_an_untouched_account_gets_zero_reviewed_not_a_made_up_rate(client):
    d = client.login("newbie").get("/stats/acceptance").json()
    assert d["reviewed"] == 0
    assert d["rate"] == 0


def test_signing_in_is_required(client):
    client.cookies.clear()
    r = client.get("/stats/acceptance", follow_redirects=False)
    assert r.status_code in (302, 303, 307, 401, 403), \
        "the acceptance endpoint answered an anonymous caller"


# ── the dashboard wiring ─────────────────────────────────────────────────────

def dash():
    from src.dashboard.aurora_html import DASHBOARD_HTML
    return DASHBOARD_HTML


def test_the_dashboard_renders_an_acceptance_rate_tile():
    html = dash()
    assert 'k="Acceptance rate"' in html


def test_the_tile_shows_a_dash_not_zero_percent_before_anything_is_judged():
    """0% is a claim about your taste. An account that has judged nothing has
    made no claim, so the tile must not make one for it."""
    html = dash()
    assert "const acceptRate = reviewed ? accept.rate : null;" in html
    assert "acceptRate===null ? '—' : acceptRate+'%'" in html


def test_a_high_rate_is_green_and_a_low_one_is_red():
    """The bands are the whole reading at a glance. Inverted, a 90% rate is
    painted the same red as a 10% one and tells the user the opposite of the
    number printed directly above it."""
    html = dash()
    assert ("acceptRate>=60 ? 'var(--live)' : "
            "acceptRate>=30 ? 'var(--pending)' : 'var(--danger)'") in html


def test_the_tile_reads_the_ledger_endpoint_and_not_the_clip_store():
    html = dash()
    assert "fetch('/stats/acceptance')" in html


def test_the_rate_refreshes_on_every_event_that_can_move_it():
    """Approve sends clip_updated, reject and clear send clip_removed, undo
    re-sends clip_ready. Miss one and the tile lies until the next reconnect."""
    html = dash()
    # Anchor inside the socket handler: refetchAll() also calls loadAcceptance,
    # and finding THAT call would pass while the live path did nothing.
    start = html.find("ws.onmessage")
    end = html.find("ws.onclose", start)
    assert start > 0 and end > start
    handler = html[start:end]
    i = handler.find("loadAcceptance();")
    assert i > 0, "the socket handler never refreshes the acceptance rate"
    # The call's OWN condition, back to the `if(` that opens it — not a fixed
    # window, which reached into the hz_ws dispatch line directly above and
    # went on passing after the guard itself was replaced with `if(false)`.
    j = handler.rfind("if(", 0, i)
    assert j > 0, "loadAcceptance() is not behind a condition at all"
    guard = handler[j:i]
    assert "msg.event" in guard, f"the guard tests something else: {guard!r}"
    for ev in ("clip_ready", "clip_updated", "clip_removed"):
        assert ev in guard, f"{ev} does not refresh the acceptance rate"


def test_the_rate_is_pulled_by_refetchall_so_it_survives_a_reconnect():
    """CLAUDE.md rule 3: fetch-on-mount state must also be in refetchAll(), or
    it goes stale after a deploy drops every socket."""
    html = dash()
    start = html.find("const refetchAll = useCallback(")
    end = html.find("const wsBootstrapped", start)
    assert start > 0 and end > start
    assert "loadAcceptance();" in html[start:end]


def test_the_refresh_is_debounced_so_clearing_a_queue_is_not_a_scan_per_clip():
    """The endpoint scans the ledger file. Clearing 40 clips fires 40 events."""
    html = dash()
    i = html.find("const loadAcceptance = useCallback(")
    assert i > 0
    body = html[i:i + 500]
    assert "clearTimeout(acceptTimer.current)" in body
    assert "setTimeout(" in body


def test_the_stat_row_has_room_for_five_tiles():
    """Four columns with five tiles leaves one stranded on a second row."""
    html = dash()
    assert ".rd-stats{display:grid;grid-template-columns:repeat(5,1fr)" in html


def test_the_odd_tile_out_spans_the_narrow_two_column_row():
    """`:nth-child(odd)` is the whole rule. Dropped, a four-tile row would put
    its last tile full width for no reason."""
    html = dash()
    assert ".rd-stats>:last-child:nth-child(odd){grid-column:1/-1}" in html
