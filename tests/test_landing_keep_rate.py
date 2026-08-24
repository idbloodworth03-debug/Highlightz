"""The keep rate published on the landing page.

Beside the clip count: the count says how much the formula caught, this says
how much of it streamers actually kept. It is a public marketing claim taken
from real user decisions, so the tests here are mostly about the ways it could
become a claim we cannot stand behind — a percentage off six clips, a 0% from
an empty ledger, or one account's numbers leaking through a shared clip id.
"""

import pytest

from src.stats import stream_stats as ss


@pytest.fixture(autouse=True)
def ledger(tmp_path, monkeypatch):
    from src.dashboard import api
    monkeypatch.setattr(ss, "_LOG_FILE", tmp_path / "stream_stats.jsonl")
    # The rate is cached for five minutes in production; a stale entry from a
    # previous test would make every assertion here a coin toss.
    monkeypatch.setattr(api, "_keep_cache", None, raising=False)
    yield tmp_path
    monkeypatch.setattr(api, "_keep_cache", None, raising=False)


def rec(event, uid="u1", channel="novafps", cid="c1"):
    ss.record(event, {"id": cid, "user_id": uid, "channel": channel})


def judged(approved, rejected, uid="u1", channel="novafps"):
    """Put `approved` kept and `rejected` thrown-out clips in the ledger."""
    n = 0
    for _ in range(approved):
        n += 1
        rec(ss.CAUGHT, uid, channel, f"a{n}")
        rec(ss.APPROVED, uid, channel, f"a{n}")
    for _ in range(rejected):
        n += 1
        rec(ss.CAUGHT, uid, channel, f"r{n}")
        rec(ss.REJECTED, uid, channel, f"r{n}")


# ── the aggregate ────────────────────────────────────────────────────────────

def test_the_rate_is_kept_over_what_was_actually_judged():
    judged(30, 10)
    t = ss.overall_totals()
    assert t["reviewed"] == 40
    assert t["kept_pct"] == 75


def test_clips_still_waiting_in_a_queue_are_not_counted_as_thrown_out():
    """Nobody turned them down. Counting them would make the published rate
    fall whenever users get busy, which says nothing about the formula."""
    judged(10, 0)
    for i in range(90):
        rec(ss.CAUGHT, cid=f"pending{i}")
    t = ss.overall_totals()
    assert t["caught"] == 100
    assert t["reviewed"] == 10
    assert t["kept_pct"] == 100


def test_a_cleared_queue_is_not_counted_as_thrown_out():
    judged(6, 2)
    for i in range(40):
        rec(ss.CAUGHT, cid=f"c{i}")
        rec(ss.CLEARED, cid=f"c{i}")
    t = ss.overall_totals()
    assert t["cleared"] == 40
    assert t["reviewed"] == 8
    assert t["kept_pct"] == 75


def test_an_undone_rejection_stops_counting_against_the_rate():
    judged(1, 1)
    assert ss.overall_totals()["kept_pct"] == 50
    rec(ss.UNDONE, cid="r2")
    assert ss.overall_totals()["kept_pct"] == 100


def test_every_account_is_added_together():
    judged(3, 1, uid="u1")
    judged(1, 3, uid="u2")
    t = ss.overall_totals()
    assert (t["approved"], t["rejected"]) == (4, 4)
    assert t["kept_pct"] == 50


def test_one_accounts_undo_cannot_retract_another_accounts_rejection():
    """Clip ids come from Twitch, not from us. A retraction set built across
    the whole ledger would let two accounts that touched the same clip cancel
    each other's rows."""
    for uid in ("u1", "u2"):
        rec(ss.CAUGHT, uid=uid, cid="dup")
        rec(ss.REJECTED, uid=uid, cid="dup")
    rec(ss.UNDONE, uid="u1", cid="dup")
    assert ss.overall_totals()["rejected"] == 1


def test_an_empty_ledger_is_zero_reviewed_not_a_crash():
    t = ss.overall_totals()
    assert t["reviewed"] == 0 and t["kept_pct"] == 0
    assert t["caught"] == 0


# ── the sample floor ─────────────────────────────────────────────────────────

def test_a_rate_from_a_handful_of_clips_is_not_published():
    """The whole reason the floor exists. Six kept clips is not evidence."""
    from src.dashboard import api
    judged(6, 0)
    kept, sample = api.public_keep_rate(force=True)
    assert sample == 6
    assert kept is None, "a 6-clip sample was published as a marketing claim"


def test_the_rate_is_published_once_there_is_enough_of_a_sample():
    from src.dashboard import api
    judged(40, 20)
    kept, sample = api.public_keep_rate(force=True)
    assert sample == 60
    assert kept == 67


def test_the_floor_is_a_sample_floor_not_a_flattering_one():
    """It must gate on how many clips were judged, never on whether the
    resulting number looks good. A bad rate over a real sample gets published;
    hiding it would make the good ones worthless."""
    from src.dashboard import api
    judged(5, 75)
    kept, sample = api.public_keep_rate(force=True)
    assert sample == 80
    assert kept == 6, "an unflattering rate over a real sample was suppressed"


def test_an_empty_ledger_publishes_nothing_rather_than_zero_percent():
    from src.dashboard import api
    kept, sample = api.public_keep_rate(force=True)
    assert kept is None and sample == 0


def test_a_broken_stats_read_withholds_the_rate_instead_of_breaking_the_page():
    """The landing page is the front door. A stats failure must cost a tile,
    not the page."""
    from src.dashboard import api
    judged(60, 20)

    def boom():
        raise OSError("disk went away")

    original = ss.overall_totals
    ss.overall_totals = boom
    try:
        kept, sample = api.public_keep_rate(force=True)
    finally:
        ss.overall_totals = original
    assert kept is None and sample == 0


def test_the_rate_is_cached_so_the_landing_page_is_not_a_ledger_scan_per_visit():
    from src.dashboard import api
    judged(40, 20)
    first = api.public_keep_rate(force=True)
    calls = []
    original = ss.overall_totals
    ss.overall_totals = lambda: (calls.append(1), original())[1]
    try:
        for _ in range(20):
            api.public_keep_rate()
    finally:
        ss.overall_totals = original
    assert calls == [], f"the ledger was scanned {len(calls)} times behind the cache"
    assert api.public_keep_rate() == first


# ── the endpoint and the page ────────────────────────────────────────────────

@pytest.fixture
def client(ledger):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    return TestClient(api.app)


def test_the_endpoint_publishes_the_rate_with_its_sample(client):
    judged(45, 15)
    d = client.get("/landing/stats").json()
    assert d["kept_pct"] == 75
    assert d["kept_sample"] == 60


def test_the_endpoint_sends_null_not_zero_below_the_floor(client):
    """0 and "no rate yet" are different facts, and the client tells them apart
    by type. Sending 0 would print "0% kept reviewed clips"."""
    judged(3, 1)
    d = client.get("/landing/stats").json()
    assert d["kept_pct"] is None


def test_the_endpoint_needs_no_login(client):
    judged(40, 20)
    r = client.get("/landing/stats")
    assert r.status_code == 200


def test_the_endpoint_exposes_no_user_identifying_data(client):
    judged(40, 20, uid="secret-user", channel="secret-channel")
    body = client.get("/landing/stats").text
    assert "secret-user" not in body and "secret-channel" not in body


def test_the_rate_is_in_the_html_of_the_first_response_not_only_the_json():
    """Crawlers and AI readers do not run the script. A rate that only exists
    after a fetch does not exist for them — the same reason the clip count is
    baked in."""
    from src.dashboard import api
    judged(40, 20)
    api._keep_cache = None
    html = api.render_landing()
    assert '<span id="lp-kept" data-kept="67">67%</span>' in html
    assert '<div class="stat stat-big" id="stat-kept">' in html


def test_the_tile_stays_hidden_in_the_html_when_there_is_no_rate():
    from src.dashboard import api
    judged(4, 0)
    api._keep_cache = None
    html = api.render_landing()
    assert '<div class="stat stat-big" id="stat-kept" style="display:none">' in html
    assert "67%" not in html


def test_the_rate_and_the_clip_count_gate_independently():
    """They answer different questions off different stores. Tying them
    together would hide a real rate because a counter file was reset."""
    from src.dashboard import api
    judged(40, 20)
    api._keep_cache = None
    api._clip_counter = 0
    try:
        html = api.render_landing()
    finally:
        api._clip_counter = None
    assert '<div class="stat stat-big" id="stat-kept">' in html, \
        "the keep rate was withheld because the clip counter was zero"
    assert 'id="stat-clips" style="display:none"' in html


def test_the_tile_sits_beside_the_clip_count():
    from src.dashboard.api import LANDING_HTML
    i = LANDING_HTML.find('id="stat-clips"')
    j = LANDING_HTML.find('id="stat-kept"')
    assert i > 0 and j > i
    between = LANDING_HTML[i:j]
    assert between.count('class="stat') == 1, \
        "another stat tile was inserted between the count and the keep rate"


def test_the_client_script_tells_a_real_zero_from_no_rate_at_all():
    """`if(d.kept_pct)` would drop a genuine 0% AND treat null as a number to
    print; the typeof check is the whole guard."""
    from src.dashboard.api import LANDING_HTML
    assert "typeof d.kept_pct==='number'" in LANDING_HTML


def test_the_script_updates_the_tile_live_for_a_long_open_tab():
    from src.dashboard.api import LANDING_HTML
    assert "kel.textContent=d.kept_pct+'%'" in LANDING_HTML
    assert "ktile.style.display=''" in LANDING_HTML
