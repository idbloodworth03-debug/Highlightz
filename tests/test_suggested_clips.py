"""Crowd suggestions: clips surfaced because VIEWERS clipped them.

WHY THE FEATURE EXISTS. Clips went viral off channels we were watching and the
bot never surfaced them. Every other path into the review queue runs through a
score, and the measurements in analyze_virality say the score is close to blind
(within-labeler r = -0.060, within-account AUC 0.547). A path that never
consults it is the only way a missed moment can reach the user at all.

WHAT THESE TESTS ARE MOSTLY ABOUT. Not the happy path — that is four asserts.
The bulk is the two ways this feature can quietly damage the product it sits
next to:

  1. It lands clips in the same review queue a user pays for. It draws on its
     own per-plan budget (max_suggested) rather than max_pending, so it cannot
     take a slot a triggered clip wanted — a guarantee that was previously a
     50% reserve and is now structural.

  2. It creates review decisions on clips the formula never produced. Feeding
     those back into learning would raise a channel's trigger threshold on the
     evidence of a clip it never claimed — making the detector fire LESS,
     exactly where the crowd is finding what it missed.

Both are silent. Neither shows up in a screenshot.
"""

import time
from datetime import datetime, timezone

import pytest

from src.trigger import suggested_clips as sc


# ── fixtures ─────────────────────────────────────────────────────────────────

def _ts(epoch: float) -> str:
    """Twitch's created_at format."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def row(slug: str, at: float, *, creator="fan", creator_id="99",
        views=0, title="a moment", duration=30.0) -> dict:
    """One Helix clip row, shaped as Get Clips actually returns it."""
    return {
        "id": slug,
        "created_at": _ts(at),
        "creator_name": creator,
        "creator_id": creator_id,
        "view_count": views,
        "title": title,
        "duration": duration,
        "url": f"https://clips.twitch.tv/{slug}",
        "embed_url": f"https://clips.twitch.tv/embed?clip={slug}",
        "thumbnail_url": f"https://cdn/{slug}.jpg",
    }


@pytest.fixture
def buf():
    sc.reset()
    yield sc.SuggestionBuffer("aceu")
    sc.reset()


T0 = 1_700_000_000.0


# ── the delay ────────────────────────────────────────────────────────────────

def test_a_fresh_clip_is_not_suggested_immediately(buf):
    """The delay the feature was asked for. A clip is suggested only after it
    has had time to collect corroboration and view counts."""
    buf.offer([row("a", T0)], set(), set(), now=T0)
    assert buf.ready(now=T0) == []
    assert buf.ready(now=T0 + sc.SETTLE_SECS - 1) == []


def test_it_is_suggested_once_it_has_settled(buf):
    buf.offer([row("a", T0, views=12)], set(), set(), now=T0)
    out = buf.ready(now=T0 + sc.SETTLE_SECS)
    assert [s.slug for s in out] == ["a"]
    assert out[0].view_count == 12
    assert out[0].channel == "aceu"
    assert out[0].url == "https://clips.twitch.tv/a"
    assert out[0].embed_url == "https://clips.twitch.tv/embed?clip=a"


def test_seeing_the_same_clip_again_does_not_restart_its_clock(buf):
    """THE ONE THAT MATTERS MOST, and the least obvious.

    The poll window (420s) is far wider than the poll interval (90s), so a
    given clip comes back on four or five consecutive polls. If a re-sighting
    refreshed first_seen, every clip would have its clock reset before it could
    ever ripen and the feature would emit NOTHING — while looking perfectly
    healthy in the logs, because rows keep arriving and the buffer keeps
    filling. Re-offering is the normal case, not the edge case."""
    buf.offer([row("a", T0)], set(), set(), now=T0)
    for tick in range(1, 5):                       # re-seen every poll
        buf.offer([row("a", T0)], set(), set(), now=T0 + 45.0 * tick)
    assert [s.slug for s in buf.ready(now=T0 + sc.SETTLE_SECS)] == ["a"], \
        "a clip that keeps reappearing never ripens"


def test_re_sighting_refreshes_the_view_count(buf):
    """Why the sink deliberately gets every row rather than only new ones: the
    later polls are free and carry a number the first one could not have."""
    buf.offer([row("a", T0, views=0)], set(), set(), now=T0)
    buf.offer([row("a", T0, views=340)], set(), set(), now=T0 + 90)
    assert buf.ready(now=T0 + sc.SETTLE_SECS)[0].view_count == 340


def test_a_view_count_never_goes_backwards(buf):
    """Helix has been seen to return a stale row on a later page. Taking the
    max means a refresh can only improve the number, never undo one."""
    buf.offer([row("a", T0, views=340)], set(), set(), now=T0)
    buf.offer([row("a", T0, views=0)], set(), set(), now=T0 + 90)
    assert buf.ready(now=T0 + sc.SETTLE_SECS)[0].view_count == 340


# ── clustering: one moment, one suggestion ───────────────────────────────────

def test_several_viewers_clipping_one_moment_produce_one_suggestion(buf):
    """Three people clipping the same play is one item for the review queue,
    not three. The count is the interesting part and it rides along."""
    buf.offer([row("a", T0, creator_id="1"),
               row("b", T0 + 8, creator_id="2"),
               row("c", T0 + 19, creator_id="3")], set(), set(), now=T0)
    out = buf.ready(now=T0 + sc.SETTLE_SECS)
    assert len(out) == 1
    assert out[0].clipper_count == 3


def test_moments_far_apart_stay_separate(buf):
    buf.offer([row("a", T0, creator_id="1"),
               row("b", T0 + sc.CLUSTER_SECS + 30, creator_id="2")],
              set(), set(), now=T0)
    assert len(buf.ready(now=T0 + sc.SETTLE_SECS)) == 2


def test_a_long_bit_chains_into_one_moment(buf):
    """Single-linkage on purpose. Viewers clipping across a 90-second bit are
    reacting to one thing; splitting it on a fixed window would put three cards
    for the same joke in the queue."""
    rows = [row(f"c{i}", T0 + i * 30, creator_id=str(i)) for i in range(4)]
    buf.offer(rows, set(), set(), now=T0)
    out = buf.ready(now=T0 + sc.SETTLE_SECS)
    assert len(out) == 1 and out[0].clipper_count == 4


def test_the_most_viewed_clip_represents_its_moment(buf):
    """View count is the only quality signal on offer that is not ours — and
    not-ours is the whole point of the feature."""
    buf.offer([row("a", T0, creator_id="1", views=5),
               row("b", T0 + 10, creator_id="2", views=900),
               row("c", T0 + 20, creator_id="3", views=40)], set(), set(), now=T0)
    out = buf.ready(now=T0 + sc.SETTLE_SECS)
    assert out[0].slug == "b" and out[0].view_count == 900


def test_ties_go_to_whoever_clipped_first(buf):
    """Brand-new clips are all on zero views, so the tie-break is the common
    case rather than a rare one. The first clipper reacted fastest, so their
    30 seconds is likeliest to contain the setup and not only the payoff."""
    buf.offer([row("late", T0 + 20, creator_id="2"),
               row("first", T0, creator_id="1")], set(), set(), now=T0)
    assert buf.ready(now=T0 + sc.SETTLE_SECS)[0].slug == "first"


def test_losing_members_of_a_cluster_do_not_come_back_alone(buf):
    """They were retired with the winner. Left in the buffer they would ripen
    on the next pass and suggest the same moment a second time."""
    buf.offer([row("a", T0, creator_id="1", views=1),
               row("b", T0 + 5, creator_id="2", views=99)], set(), set(), now=T0)
    assert len(buf.ready(now=T0 + sc.SETTLE_SECS)) == 1
    assert buf.ready(now=T0 + sc.SETTLE_SECS + 600) == []


def test_a_later_clip_of_an_already_suggested_moment_is_ignored(buf):
    """The slug set alone cannot catch this: a fourth viewer clipping the same
    play three minutes later has a DIFFERENT slug, forms a fresh cluster of
    one, and would suggest the moment all over again. Emitted moments are
    remembered by time, not just by id."""
    buf.offer([row("a", T0, creator_id="1")], set(), set(), now=T0)
    assert len(buf.ready(now=T0 + sc.SETTLE_SECS)) == 1
    buf.offer([row("d", T0 + 10, creator_id="4")], set(), set(),
              now=T0 + sc.SETTLE_SECS + 200)
    assert buf.ready(now=T0 + sc.SETTLE_SECS + 500) == [], \
        "the same moment was suggested twice under a different slug"


# ── never learn from ourselves ───────────────────────────────────────────────

def test_our_own_clips_are_not_suggested_back_to_us(buf):
    """Our clips are real Twitch clips and come back in the same Get Clips
    response. Suggesting them would be the bot recommending its own work as
    something it missed."""
    buf.offer([row("ours", T0, creator_id="42")], {"42"}, set(), now=T0)
    assert buf.ready(now=T0 + sc.SETTLE_SECS) == []


def test_our_own_clips_are_excluded_by_stored_slug_too(buf):
    """Both checks, because either identifier can be missing — a user whose
    twitch_id we do not hold, or a slug we failed to record."""
    buf.offer([row("ours", T0, creator_id="unknown")], set(), {"ours"}, now=T0)
    assert buf.ready(now=T0 + sc.SETTLE_SECS) == []


# ── caps and expiry ──────────────────────────────────────────────────────────

def test_a_clip_happy_channel_cannot_flood_the_queue(buf):
    """The review queue is a human's attention. A busy chat produces dozens of
    clips an hour and the cap is what stops all of them arriving."""
    # Spaced well beyond CLUSTER_SECS so each is its own moment, but all
    # OFFERED at T0 — first_seen is what ripens, and reading them long enough
    # after their creation times to age them past MAX_AGE_SECS would expire the
    # lot and let an empty result masquerade as a working cap.
    rows = [row(f"c{i}", T0 + i * 300, creator_id=str(i)) for i in range(20)]
    buf.offer(rows, set(), set(), now=T0)
    out = buf.ready(now=T0 + sc.SETTLE_SECS)
    assert len(out) == sc.MAX_PER_HOUR


def test_the_cap_lifts_once_the_hour_rolls(buf):
    rows = [row(f"c{i}", T0 + i * 300, creator_id=str(i)) for i in range(20)]
    buf.offer(rows, set(), set(), now=T0)
    assert len(buf.ready(now=T0 + sc.SETTLE_SECS)) == sc.MAX_PER_HOUR
    later = T0 + sc.SETTLE_SECS + 3601
    buf.offer([row("fresh", later, creator_id="z")], set(), set(), now=later)
    assert len(buf.ready(now=later + sc.SETTLE_SECS)) == 1


def test_a_capped_moment_is_dropped_rather_than_held(buf):
    """Holding it would surface a stale moment an hour later. The ask was for
    what the crowd is clipping now."""
    rows = [row(f"c{i}", T0 + i * 300, creator_id=str(i)) for i in range(20)]
    buf.offer(rows, set(), set(), now=T0)
    buf.ready(now=T0 + sc.SETTLE_SECS)
    assert buf.pending_count == 0


def test_stale_candidates_are_dropped(buf):
    """Covers a worker restart mid-ripen, and stops the buffer growing across a
    long broadcast."""
    buf.offer([row("a", T0)], set(), set(), now=T0)
    assert buf.ready(now=T0 + sc.MAX_AGE_SECS + 1) == []
    assert buf.pending_count == 0


def test_junk_timestamps_are_skipped(buf):
    bad = row("a", T0)
    bad["created_at"] = "not a date"
    buf.offer([bad], set(), set(), now=T0)
    assert buf.ready(now=T0 + sc.SETTLE_SECS) == []


def test_an_empty_poll_is_harmless(buf):
    buf.offer([], set(), set(), now=T0)
    buf.offer(None, set(), set(), now=T0)
    assert buf.ready(now=T0 + sc.SETTLE_SECS) == []


def test_the_buffer_is_shared_per_channel():
    """Five users watching one streamer share the poll, so they must share the
    buffer and the cap — otherwise the channel emits five copies of every
    suggestion and blows through the cap five times over."""
    sc.reset()
    assert sc.buffer_for("aceu") is sc.buffer_for("aceu")
    assert sc.buffer_for("aceu") is not sc.buffer_for("jynxzi")
    sc.reset()


# ── the sink on the existing poll ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_sink_sees_every_row_not_just_the_new_ones(monkeypatch, tmp_path):
    """The learning log dedupes to one record per clip. The suggester must NOT
    inherit that: the repeats are where the fresher view counts live."""
    from src.trigger import viewer_clips as vc
    monkeypatch.setattr(vc, "_LOG_FILE", tmp_path / "v.jsonl")
    vc._seen.clear()
    vc._last_poll.clear()
    rows = [row("a", T0), row("b", T0 + 5)]
    _fake_helix(monkeypatch, rows)

    seen = []
    await vc.poll_and_record("aceu", "1", None, set(), set(),
                             on_rows=lambda r: seen.append(list(r)))
    await vc.poll_and_record("aceu", "1", None, set(), set(),
                             on_rows=lambda r: seen.append(list(r)))
    assert len(seen) == 2
    assert [c["id"] for c in seen[1]] == ["a", "b"], \
        "the second poll handed the sink nothing — repeats were filtered out"


@pytest.mark.asyncio
async def test_a_broken_sink_cannot_stop_the_learning_record(monkeypatch, tmp_path):
    """The learning log predates this feature and has ~79k records in it. A
    suggester bug must not be able to interrupt it — nor, further up, clipping."""
    from src.trigger import viewer_clips as vc
    log_file = tmp_path / "v.jsonl"
    monkeypatch.setattr(vc, "_LOG_FILE", log_file)
    vc._seen.clear()
    vc._last_poll.clear()
    _fake_helix(monkeypatch, [row("a", T0)])

    def boom(_rows):
        raise RuntimeError("suggester exploded")

    n = await vc.poll_and_record("aceu", "1", None, set(), set(), on_rows=boom)
    assert n == 1, "a sink failure swallowed the learning record"
    assert log_file.exists() and log_file.read_text().strip()


@pytest.mark.asyncio
async def test_the_poll_is_unchanged_when_nobody_is_listening(monkeypatch, tmp_path):
    """The sink is optional and defaults to off, so the learning path behaves
    exactly as it did before this feature existed."""
    from src.trigger import viewer_clips as vc
    monkeypatch.setattr(vc, "_LOG_FILE", tmp_path / "v.jsonl")
    vc._seen.clear()
    vc._last_poll.clear()
    _fake_helix(monkeypatch, [row("a", T0), row("b", T0 + 5)])
    assert await vc.poll_and_record("aceu", "1", None, set(), set()) == 2


def _fake_helix(monkeypatch, rows):
    """Stand in for aiohttp + Helix inside poll_and_record."""
    import sys, types

    class _Resp:
        status = 200
        async def json(self): return {"data": rows}
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    class _Session:
        def get(self, *a, **k): return _Resp()
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    fake = types.ModuleType("aiohttp")
    fake.ClientSession = lambda *a, **k: _Session()
    monkeypatch.setitem(sys.modules, "aiohttp", fake)

    async def _tok(_s): return "t"
    from src.output import twitch_clips
    monkeypatch.setattr(twitch_clips, "_get_app_token", _tok)


# ── landing in the queue: the reserve ────────────────────────────────────────

class _Cfg:
    channel = "aceu"
    platform_name = "twitch"
    user_id = "punter"
    preset = "default"


def _worker_stub():
    """Enough of a StreamWorker to exercise _land_suggestions without building
    a platform, a queue and a chat monitor."""
    from src.ingestion.stream_worker import StreamWorker

    class _Stub:
        _config = _Cfg()
        _stream_info = None
        _land_suggestions = StreamWorker._land_suggestions
    return _Stub()


class _FakeBuf:
    def __init__(self, items): self._items = items
    def ready(self): return self._items


def _suggestion(slug, at=T0, **kw):
    return sc.Suggestion(slug=slug, url=f"https://clips.twitch.tv/{slug}",
                         embed_url=f"https://e/{slug}", thumbnail_url="",
                         title="t", creator="fan", created_at=at,
                         view_count=kw.get("views", 0),
                         clipper_count=kw.get("clippers", 1),
                         duration=30.0, channel="aceu")


@pytest.mark.asyncio
async def test_suggestions_stop_at_their_own_budget(monkeypatch):
    """THEIR OWN BUDGET, not a slice of the review queue.

    This used to assert a 50% RESERVE of max_pending, because suggestions
    shared the pending queue with triggered clips and a full queue drops the
    newest arrival — so unreserved, a chatty channel's suggestions could be the
    reason a clip the user pays for never landed. `max_suggested` makes that
    guarantee structurally instead of arithmetically: they are not drawing on
    max_pending at all, so no reserve is needed to keep them off it."""
    from src.dashboard import api

    landed = []
    async def _ready(clip): landed.append(clip)
    monkeypatch.setattr(api, "notify_clip_ready", _ready)
    monkeypatch.setattr(api, "suggestion_room", lambda uid: (0, 3))

    buf = _FakeBuf([_suggestion(f"s{i}", at=T0 + i * 600) for i in range(9)])
    await _worker_stub()._land_suggestions(buf)

    assert len(landed) == 3, "suggestions ran past their budget"


@pytest.mark.asyncio
async def test_a_budget_already_spent_takes_no_more(monkeypatch):
    from src.dashboard import api
    landed = []
    async def _ready(clip): landed.append(clip)
    monkeypatch.setattr(api, "notify_clip_ready", _ready)
    monkeypatch.setattr(api, "suggestion_room", lambda uid: (3, 3))
    await _worker_stub()._land_suggestions(_FakeBuf([_suggestion("s1")]))
    assert landed == []


@pytest.mark.asyncio
async def test_suggestions_never_consume_the_pending_queue(monkeypatch):
    """The guarantee the reserve was approximating, now asserted directly: a
    queue full to its cap of TRIGGERED clips does not stop a suggestion, and a
    pile of suggestions does not eat into what triggered clips may use."""
    from src.dashboard import api
    api._clips.clear()
    try:
        for i in range(20):                       # free cap, entirely triggered
            api._clips[f"p{i}"] = {"id": f"p{i}", "user_id": "punter",
                                   "status": "pending", "trigger_score": 80}
        for i in range(2):
            api._clips[f"s{i}"] = {"id": f"s{i}", "user_id": "punter",
                                   "status": "pending", "suggested": True}
        monkeypatch.setattr(api, "limits_for",
                            lambda u: {"max_pending": 20, "max_suggested": 3},
                            raising=False)
        from src.billing import plans
        monkeypatch.setattr(plans, "limits_for",
                            lambda u: {"max_pending": 20, "max_suggested": 3})
        used_p, cap_p = api.pending_room("punter")
        used_s, cap_s = api.suggestion_room("punter")
        assert (used_p, cap_p) == (20, 20), "suggestions were counted as pending clips"
        assert (used_s, cap_s) == (2, 3), "triggered clips were counted as suggestions"
    finally:
        api._clips.clear()


@pytest.mark.asyncio
async def test_a_landed_suggestion_carries_no_score_and_says_who_made_it(monkeypatch):
    from src.dashboard import api
    landed = []
    async def _ready(clip): landed.append(clip)
    monkeypatch.setattr(api, "notify_clip_ready", _ready)
    monkeypatch.setattr(api, "suggestion_room", lambda uid: (0, 3))

    await _worker_stub()._land_suggestions(
        _FakeBuf([_suggestion("s1", views=120, clippers=3)]))

    c = landed[0]
    assert c["suggested"] is True
    assert c["suggested_by"] == "fan" and c["clipper_count"] == 3
    assert c["suggested_views"] == 120
    assert c["twitch_clip_id"] == "s1"
    assert c["trigger_score"] == 0.0 and c["virality_score"] == 0.0, \
        "a score was invented for a clip no score produced"
    assert c["trigger_signals"] == []
    # The MOMENT, not when we got round to processing it. The review queue
    # sorts on this, and notify_clip_ready's dedup window uses it to recognise
    # a moment we already clipped ourselves.
    assert c["created_at"] == T0


# ── inert to every learning and reporting path ───────────────────────────────

@pytest.fixture
def api_client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    import base64, json as _j
    from src.dashboard import api
    from src.auth import users as user_store
    from src.profiles import manager as pm
    from src.stats import stream_stats as ss
    from src.profiles import training_log

    who = {"id": "punter", "username": "punter",
           "subscription_status": "active", "plan": "pro"}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: who)
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)

    taught, stats, trained, missed = [], [], [], []
    monkeypatch.setattr(ss, "record", lambda kind, clip: stats.append(kind))
    monkeypatch.setattr(training_log, "log_outcome",
                        lambda clip, label: trained.append(label))

    async def _missed(uid, ch, reason="queue_full"): missed.append(ch)
    monkeypatch.setattr(api, "notify_clip_missed", _missed)

    class _Profile:
        def record_clip(self, approved, signals): taught.append(approved)
        def to_dict(self): return {}

    class _PM:
        async def load(self, ch): return _Profile()
        async def save(self, p): return None
    monkeypatch.setattr(pm, "get_profile_manager", lambda uid: _PM())

    api._clips.clear()
    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "punter", "username": "punter",
         "is_admin": False, "subscription_status": "active"}).encode())).decode())
    c.api, c.taught, c.stats, c.trained, c.missed = api, taught, stats, trained, missed
    yield c
    api._clips.clear()


def _seed(api, **kw):
    clip = {"id": "sug1", "user_id": "punter", "platform": "twitch",
            "channel": "aceu", "status": "pending", "created_at": T0,
            "clip_title": "a moment", "trigger_score": 0.0,
            "trigger_signals": [], "suggested": True, "suggested_by": "fan",
            "clipper_count": 2, "twitch_url": "https://clips.twitch.tv/s1"}
    clip.update(kw)
    api._clips["sug1"] = clip
    return clip


def test_approving_a_suggestion_does_not_teach_the_channel(api_client):
    """The formula did not produce this clip, so the decision says nothing
    about whether the formula was right."""
    _seed(api_client.api)
    assert api_client.post("/clips/sug1/approve").status_code == 200
    assert api_client.taught == [], "a suggestion drifted the channel's profile"
    assert api_client.trained == [], "a suggestion wrote a training row"


def test_rejecting_a_suggestion_does_not_raise_the_trigger_threshold(api_client):
    """THE SHARP EDGE. record_clip(approved=False) adds +0.75 to the channel's
    trigger threshold. Letting a rejected suggestion through would punish the
    detector for a moment it never claimed and make it fire LESS — on exactly
    the channel where the crowd is finding what it missed. The damage runs
    backwards, which is why it would never look like this feature's fault."""
    _seed(api_client.api)
    assert api_client.post("/clips/sug1/reject").status_code == 200
    assert api_client.taught == [], "rejecting a suggestion raised the threshold"
    assert api_client.trained == []


def test_a_normal_clip_still_teaches_the_channel(api_client):
    """The guard has to be narrow. If it caught everything, review would stop
    training the detector at all and nothing would say so."""
    _seed(api_client.api, suggested=False, trigger_score=71.0,
          trigger_signals=[{"type": "KEYWORD", "value": 0.8}])
    assert api_client.post("/clips/sug1/approve").status_code == 200
    assert api_client.taught == [True]
    assert api_client.trained, "a real approval stopped writing training data"


@pytest.mark.asyncio
async def test_a_suggestion_is_not_counted_as_a_clip_we_caught(api_client):
    """CAUGHT means our detector found the moment. Counting a suggestion
    inflates the per-channel number shown to streamers, in the direction that
    flatters us, and corrupts the acceptance rate on the dashboard — which is a
    ratio over these records with suggestions already excluded from the other
    end of it."""
    api = api_client.api
    before = api.get_clip_counter()
    await api.notify_clip_ready({
        "id": "s9", "user_id": "punter", "channel": "aceu", "platform": "twitch",
        "status": "pending", "created_at": T0, "suggested": True})
    assert api_client.stats == [], "a suggestion was counted as CAUGHT"
    assert api.get_clip_counter() == before, \
        "a suggestion inflated the public clip counter"


@pytest.mark.asyncio
async def test_a_real_clip_is_still_counted(api_client):
    api = api_client.api
    before = api.get_clip_counter()
    await api.notify_clip_ready({
        "id": "r9", "user_id": "punter", "channel": "aceu", "platform": "twitch",
        "status": "pending", "created_at": T0, "trigger_score": 64.0})
    # Against the real constant. The first draft of this line read
    # `[api.stream_stats.CAUGHT if hasattr(api, "stream_stats") else stats[0]]`
    # — and api.stream_stats does not exist, because the module imports it
    # inside the function. So the fallback fired every time and the assertion
    # degenerated to stats == [stats[0]], which is true of ANY one-element list.
    # It would have passed just as happily on a suggestion being counted.
    from src.stats import stream_stats
    assert api_client.stats == [stream_stats.CAUGHT]
    assert api.get_clip_counter() == before + 1


@pytest.mark.asyncio
async def test_a_dropped_suggestion_does_not_nag_about_a_missed_clip(api_client, monkeypatch):
    """"You missed a clip" is a claim the product failed the user, and it
    drives an upgrade prompt. Nothing they pay for was lost when a bonus did
    not fit, and the reserve exists so a suggestion is never what fills the
    queue. Nagging here would manufacture a miss to sell against."""
    api = api_client.api
    monkeypatch.setattr(api, "limits_for", lambda u: {"max_pending": 1},
                        raising=False)
    from src.billing import plans
    monkeypatch.setattr(plans, "limits_for", lambda u: {"max_pending": 1})
    api._clips["filler"] = {"id": "filler", "user_id": "punter", "status": "pending",
                            "channel": "aceu", "created_at": T0 - 9999}
    await api.notify_clip_ready({
        "id": "s9", "user_id": "punter", "channel": "aceu", "platform": "twitch",
        "status": "pending", "created_at": T0, "suggested": True})
    assert api_client.missed == [], "a dropped suggestion nagged the user"


@pytest.mark.asyncio
async def test_a_dropped_real_clip_still_nags(api_client, monkeypatch):
    api = api_client.api
    from src.billing import plans
    monkeypatch.setattr(plans, "limits_for", lambda u: {"max_pending": 1})
    api._clips["filler"] = {"id": "filler", "user_id": "punter", "status": "pending",
                            "channel": "aceu", "created_at": T0 - 9999}
    await api.notify_clip_ready({
        "id": "r9", "user_id": "punter", "channel": "aceu", "platform": "twitch",
        "status": "pending", "created_at": T0, "trigger_score": 64.0})
    assert api_client.missed == ["aceu"], "a genuinely missed clip went unreported"


# ── how it looks, and the one thing it must never say ────────────────────────

from pathlib import Path
import re as _re

SRC = Path("src/dashboard/aurora_html.py").read_text()
CSS = SRC.split('<script type="text/babel">')[0]
JS = SRC.split('<script type="text/babel">')[1]


def _fn(name: str) -> str:
    m = _re.search(r"function " + name + r"\(.*?\n\}\n\n", JS, _re.S)
    assert m, f"{name} not found"
    return m.group(0)


def _code(block: str) -> str:
    """The block with comments stripped.

    A prose comment explaining a badge contains the badge's words, so an
    assertion can match the explanation instead of the markup and pass against
    a component that renders nothing. That has happened here before."""
    block = _re.sub(r"\{/\*.*?\*/\}", "", block, flags=_re.S)
    return _re.sub(r"//[^\n]*", "", block)


def test_a_suggested_clip_shows_neither_score_badge():
    """THE CORRECTNESS FIX INSIDE THE COSMETIC ONE. A suggestion carries
    trigger_score 0 because no score was consulted to surface it, and the badge
    was unconditional — so the card rendered "0% trigger". That does not read as
    "not scored", it reads as "the detector rated this worthless", on the one
    card type whose whole purpose is carrying moments the detector MISSED, six
    inches from an Approve button."""
    body = _code(_fn("RdClip"))
    # BOUND TO THE CLIP, not to a constant. Mutation testing caught this: with
    # `const sug = false;` every structural assertion below still passed,
    # because the branches are all still in the file — they just never taken.
    # "The element is written down" is not "the element is drawn".
    assert "const sug = !!clip.suggested;" in body, \
        "the suggested flag is no longer read from the clip"
    assert "rd-sugbadge" in body
    m = _re.search(r"\{sug\s*\?(.*?)\{score\}% trigger", body, _re.S)
    assert m, "the trigger badge is no longer behind a suggested check"
    assert "rd-sugbadge" in m.group(1), \
        "a suggestion does not get the badge that replaces the score"
    assert "!sug && clip.virality_score>0" in body, \
        "the virality badge can still render on a clip that has no virality score"


def test_the_modal_suppresses_the_score_too():
    """Same lie, one click further in."""
    body = _code(_fn("ClipModal"))
    assert "const sug = !!clip.suggested;" in body, \
        "the modal's suggested flag is no longer read from the clip"
    assert _re.search(r"\{sug\s*\n?\s*\?.*?rd-sugbadge", body, _re.S), \
        "the modal still shows a trigger badge on a suggestion"


def test_the_modal_does_not_invent_a_reason_it_fired():
    """sigKeys is a FIXED list of four, so an empty trigger_signals rendered
    four bars at 0% under a heading reading "Why it fired" — a fabricated
    explanation of a decision that nothing made. A suggestion did not fire."""
    body = _code(_fn("ClipModal"))
    assert "{sug?'Why it is here':'Why it fired'}" in body, \
        "the panel still claims a suggestion fired"
    assert _re.search(r"\{sug\s*\n?\s*\?", body), \
        "the signal bars are not behind a suggested check"


def test_the_card_says_whose_clip_it_actually_is():
    """We did not create it. A viewer did, on their own Twitch account, and the
    card is the only place in the product that can say so."""
    body = _code(_fn("RdClip"))
    assert "rd-sugby" in body and "clip.suggested_by" in body


def test_the_glow_exists_and_is_gold():
    """Asked for: bright, glowing, and obviously different. Gold because
    nothing else in the grid is — the viral badge is orange-to-pink and the
    crowd-clipped badge is green-to-teal."""
    # Assert on the DECLARATIONS, not on the selector appearing somewhere.
    # Mutation testing caught this: renaming the glow rule's selector left this
    # test green, because a second `.rd-clip.suggested{position:relative}` rule
    # further down still matched the bare `in CSS` check and `.split()` picked
    # up whichever came first. The two rules are now merged into one, and this
    # reads the declarations that actually make the card glow.
    rule = CSS.split(".rd-clip.suggested{")[1].split("}")[0]
    assert "box-shadow" in rule and "border-color" in rule, \
        "the suggested card lost its glow"
    assert "255,168,0" in rule or "255,197,61" in rule, "the glow is no longer gold"
    badge = CSS.split(".rd-sugbadge{")[1].split("}")[0]
    assert "ffd45e" in badge or "ff9d00" in badge, "the badge is no longer gold"
    assert "@keyframes sugpulse" in CSS


def test_the_glow_costs_nothing_while_a_clip_is_playing():
    """These badges sit over a playing clip. A backdrop-filter there re-blurs
    that patch of video on every decoded frame, which is what made scrolling a
    full queue stutter (45.6fps, 29% dropped) before that was stripped out. The
    pulse animates opacity on a pseudo-element instead, and stops entirely once
    a player is open."""
    for sel in [".rd-clip.suggested{", ".rd-clip.suggested::after{", ".rd-sugbadge{"]:
        rule = CSS.split(sel)[1].split("}")[0]
        assert "backdrop-filter" not in rule, f"{sel} reintroduced a blur layer"
    assert "body.hz-player .rd-clip.suggested::after{animation:none" in CSS, \
        "the pulse keeps repainting while a clip is playing"


def test_the_pulse_respects_reduced_motion():
    block = CSS.split("@media(prefers-reduced-motion:reduce){")[1].split("}\n}")[0]
    assert "sug" in block or "animation:none" in block


def test_the_pulse_cannot_swallow_the_click():
    """It is an overlay across the whole card, and the card is the play
    target."""
    rule = CSS.split(".rd-clip.suggested::after{")[1].split("}")[0]
    assert "pointer-events:none" in rule


@pytest.mark.asyncio
async def test_the_race_guard_caps_suggestions_on_their_own_budget(api_client):
    """notify_clip_ready re-checks the cap because the worker's check and this
    one are not atomic. It has to check the SUGGESTION budget for a suggestion,
    not the pending one — mutation testing found nothing driving this: the
    worker-level test covers the worker and suggestion_room covers the count,
    but the second check could quietly fall back to max_pending and both stayed
    green. On free that is the difference between 3 suggestions and 20."""
    api = api_client.api
    from src.billing import plans
    monkeypatch_limits = {"max_pending": 20, "max_suggested": 3, "label": "Free",
                          "vod": False, "uploads": False, "max_streams": 1}
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(plans, "limits_for", lambda u: monkeypatch_limits)
    try:
        for i in range(6):
            await api.notify_clip_ready({
                "id": f"s{i}", "user_id": "punter", "channel": "aceu",
                "platform": "twitch", "status": "pending",
                "created_at": T0 + i * 500,     # past the dedup window
                "suggested": True, "suggested_by": "fan"})
        held = [c for c in api._clips.values() if c.get("suggested")]
        assert len(held) == 3, \
            f"the suggestion budget was not enforced at the race guard: {len(held)}"
    finally:
        mp.undo()


@pytest.mark.asyncio
async def test_the_race_guard_still_lets_triggered_clips_use_the_full_queue(api_client):
    """The other half. Capping suggestions at 3 must not cap real clips at 3."""
    api = api_client.api
    from src.billing import plans
    limits = {"max_pending": 20, "max_suggested": 3, "label": "Free",
              "vod": False, "uploads": False, "max_streams": 1}
    import pytest as _pytest
    mp = _pytest.MonkeyPatch()
    mp.setattr(plans, "limits_for", lambda u: limits)
    try:
        for i in range(8):
            await api.notify_clip_ready({
                "id": f"r{i}", "user_id": "punter", "channel": "aceu",
                "platform": "twitch", "status": "pending",
                "created_at": T0 + i * 500, "trigger_score": 70.0})
        held = [c for c in api._clips.values() if not c.get("suggested")]
        assert len(held) == 8, \
            f"triggered clips were capped at the suggestion budget: {len(held)}"
    finally:
        mp.undo()
