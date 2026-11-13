"""The two caps, and who each of them refuses.

THERE ARE TWO, and conflating them was a real bug. What costs the box is a
LIVE stream: two OS subprocesses, a chat socket, a scoring loop. A REGISTERED
channel whose streamer is offline costs an is_live poll every 30 seconds and
nothing else — and offline is the normal state, because people queue an
evening's roster hours ahead of it starting.

  max_concurrent_streams  LIVE streams. The hardware ceiling, enforced at
                          go-live by acquire_live_slot(). Derived from cores.
  max_registered_streams  REGISTERED channels. Enforced by
                          _check_server_capacity() when somebody adds one.
                          Deliberately loose; registration is nearly free.

The hardware ceiling used to guard registrations, which refused people for
queueing channels that were costing nothing. Prod: 8 channels registered,
load average 0.00, 95% idle.

WHO IT REFUSED was the other half. A flat `len(_streams) >= cap` is
first-come-first-served, so two Pro users at ten channels each fill the pool
and the third customer is refused their very first stream — while the landing
page, the pricing cards and the FAQ all sell "10 channels at once". The person
turned away is the one using nothing.
"""

import pytest

from src.dashboard import api


@pytest.fixture
def pool(monkeypatch):
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api.settings, "max_registered_streams", 20)

    def fill(uid, n):
        for i in range(n):
            api._streams[f"{uid}:ch{i}_{uid}"] = {"channel": f"ch{i}_{uid}", "user_id": uid}
    return fill


def _refused(uid):
    try:
        api._check_server_capacity(uid)
        return None
    except api.HTTPException as e:
        return e.status_code


def test_a_new_customer_is_not_refused_because_two_heavy_users_got_there_first(pool):
    """The headline case. Two Pro users at 10 each = 18 of 20 before this even
    gets interesting; the third customer's FIRST channel must still start."""
    pool("heavy1", 9)
    pool("heavy2", 9)
    assert _refused("newcomer") is None


def test_two_pro_users_alone_are_not_cut_back(pool):
    """With cap=20 and two users the pool exactly fits what they bought, so
    fair share IS ten each and neither should be refused. Fairness must not
    invent scarcity that is not there."""
    pool("pro1", 9)
    pool("pro2", 9)
    assert _refused("pro1") is None


def test_the_heavy_user_is_the_one_cut_back_when_the_pool_is_tight(pool):
    """Three users, cap 20, so fair share is 6. Degrade onto whoever is using
    the most, not whoever happened to ask last."""
    pool("heavy", 9)
    pool("mid", 8)
    pool("light", 1)            # 18 of 20 — headroom is inside the reserve
    assert _refused("heavy") == 429


def test_the_light_user_still_gets_slots_while_the_heavy_one_waits(pool):
    """The whole point: scarcity lands on the biggest consumer, and the person
    well under their share carries on."""
    pool("heavy", 9)
    pool("mid", 8)
    pool("light", 1)
    assert _refused("light") is None


def test_plan_limits_govern_while_there_is_real_headroom(pool):
    """Fair-sharing must not kick in early — a Pro user alone on a quiet box
    gets all ten channels, which is exactly what was sold."""
    pool("solo", 9)
    assert _refused("solo") is None


def test_fair_share_does_not_apply_while_the_box_is_quiet(pool):
    """The promise-preserving half. Three users on a mostly-empty box gives a
    fair share of 6 — but a Pro customer paid for 10 and there is plenty of
    room, so they must get all 10. Fairness is a scarcity rule; applying it
    unconditionally would break the headline claim on an idle server."""
    pool("pro", 9)
    pool("small1", 1)
    pool("small2", 1)               # 11 of 20 — nowhere near tight
    assert _refused("pro") is None, \
        "fair-share kicked in with 9 slots free and cut a Pro user below plan"


def test_a_genuinely_full_pool_still_refuses_everyone(pool):
    """The guard has to still guard. Fairness is about ordering, not about
    pretending there is capacity that does not exist."""
    pool("a", 10)
    pool("b", 10)
    assert _refused("newcomer") == 503
    assert _refused("a") == 503


def test_the_refusal_says_whose_limit_it_is(pool):
    """The old message was "Server stream capacity reached", which reads as
    "you hit your plan limit" to someone who just paid for ten channels."""
    pool("a", 10)
    pool("b", 10)
    with pytest.raises(api.HTTPException) as e:
        api._check_server_capacity("newcomer")
    assert "not your plan" in str(e.value.detail).lower()


def test_capacity_pressure_is_logged_before_customers_notice(pool, capsys):
    pool("a", 9)
    pool("b", 8)                     # 17 of 20 = 85%, past the warn line
    api._check_server_capacity("newcomer")
    # capsys, not caplog: structlog writes to stdout and never reaches the
    # stdlib handlers caplog installs.
    out = capsys.readouterr().out
    assert "server_capacity_high" in out
    assert "total=17" in out and "cap=20" in out


def test_the_cap_is_env_tunable_without_a_deploy(pool, monkeypatch):
    """Raising it is a capacity decision that needs measurement on the real box,
    so it must be changeable in .env rather than requiring a code change."""
    from config.settings import Settings
    assert "max_concurrent_streams" in Settings.model_fields
    assert "max_registered_streams" in Settings.model_fields
    monkeypatch.setattr(api.settings, "max_registered_streams", 40)
    pool("a", 10); pool("b", 10)
    assert _refused("newcomer") is None, "cap did not follow the setting"


def test_a_newcomer_is_never_refused_while_any_room_exists(pool):
    """The guarantee, across every pool shape rather than one example. It holds
    because `fair` is at least 1 and a newcomer has 0, so the fair-share branch
    cannot catch them — worth asserting directly, since the arithmetic that
    makes it true is easy to break while editing."""
    for shape in ([19], [10, 9], [7, 7, 5], [5, 5, 5, 4], [1] * 19):
        api._streams.clear()
        for i, n in enumerate(shape):
            pool(f"u{i}", n)
        assert sum(len(k) > 0 for k in api._streams) == sum(shape)
        assert _refused("brand_new") is None, f"newcomer refused with pool {shape}"


def test_add_stream_actually_calls_the_capacity_check():
    """Every test above drives the helper directly, so reverting add_stream to
    a flat `len(_streams) >= cap` leaves them all green. Assert the wiring."""
    import inspect
    src = inspect.getsource(api.add_stream)
    assert "_check_server_capacity(uid)" in src, \
        "add_stream no longer routes through the fair-share check"
    assert "len(_streams) >= settings.max_concurrent_streams" not in src, \
        "the flat first-come-first-served check is back"


# ── the ceiling tracks the hardware ──────────────────────────────────────────
# It was hardcoded at 20, which was only ever right for the box it was typed
# on — and it was not right for that one either. Measured on the original
# 1-vCPU droplet: eight live streams sat at 93.4% of a single core. So 20 was a
# promise the machine could not keep, and it would have stayed 20 after an
# upgrade to eight times the hardware. It now derives from the cores actually
# available, in both directions.

import os

from config.settings import (Settings, _usable_cpus, _STREAMS_PER_CPU,
                             _MIN_CONCURRENT_STREAMS, _MAX_CONCURRENT_STREAMS,
                             default_max_concurrent_streams)


def test_the_ceiling_scales_with_the_machine(monkeypatch):
    """The whole point: a bigger droplet carries more streams without anyone
    editing a constant."""
    seen = {}
    for cpus in (1, 2, 4, 8, 16):
        monkeypatch.setattr("config.settings._usable_cpus", lambda c=cpus: c)
        seen[cpus] = default_max_concurrent_streams()
    assert seen[2] > seen[1], "twice the cores does not carry more streams"
    assert seen[8] > seen[4] > seen[2]
    assert seen[4] == 4 * _STREAMS_PER_CPU


def test_the_per_core_figure_is_the_measured_one_with_headroom():
    """8 live streams measured at 93.4% of one core. Anything at or above that
    is saturation, and a box at 100% stops answering rather than degrading."""
    assert _STREAMS_PER_CPU <= 8, (
        f"{_STREAMS_PER_CPU} per core is at or past the measured saturation "
        f"point; there is nothing left for the web app or the chat sockets")
    assert _STREAMS_PER_CPU >= 4, "the box is being wasted"


def test_a_nonsense_cpu_count_cannot_uncap_the_box(monkeypatch):
    monkeypatch.setattr("config.settings._usable_cpus", lambda: 100000)
    assert default_max_concurrent_streams() == _MAX_CONCURRENT_STREAMS
    monkeypatch.setattr("config.settings._usable_cpus", lambda: 0)
    assert default_max_concurrent_streams() == _MIN_CONCURRENT_STREAMS


def test_an_explicit_setting_still_wins(monkeypatch):
    """The derivation is a default, not a policy. The owner must be able to pin
    it when a box behaves differently from the measurement."""
    monkeypatch.setenv("MAX_CONCURRENT_STREAMS", "137")
    assert Settings().max_concurrent_streams == 137


def test_an_unset_value_is_resolved_rather_than_left_at_zero():
    """0 is the sentinel for "derive it". If it ever reached the capacity guard
    as 0, max(1, cap) would silently cap the entire server at one stream."""
    s = Settings()
    assert s.max_concurrent_streams >= _MIN_CONCURRENT_STREAMS
    assert s.max_concurrent_streams == default_max_concurrent_streams()


def test_a_cpu_quota_beats_the_host_core_count(tmp_path, monkeypatch):
    """os.cpu_count() reports the HOST's cores. Inside a container with a quota
    that would hand a half-core process the ceiling of a 32-core host."""
    cg = tmp_path / "cpu.max"
    cg.write_text("200000 100000")          # 2 cores
    real_open = open

    def fake_open(path, *a, **k):
        if path == "/sys/fs/cgroup/cpu.max":
            return real_open(cg, *a, **k)
        raise FileNotFoundError(path)

    monkeypatch.setattr("builtins.open", fake_open)
    assert _usable_cpus() == 2


def test_the_capacity_guard_reads_the_setting_rather_than_the_constant():
    """The guard must not go back to its own hardcoded number."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api._check_server_capacity)
    assert "settings.max_registered_streams" in src, \
        "the registration guard is back on the hardware ceiling"
    assert "settings.max_concurrent_streams" in inspect.getsource(api.acquire_live_slot), \
        "the live gate no longer reads the hardware ceiling"


# ── the live gate ────────────────────────────────────────────────────────────
# The half that protects the hardware. Registration is cheap and loosely
# bounded, so what stops a queued roster all going live at once and burying the
# box is admission control at go-live, not the registration cap.

@pytest.fixture
def slots(monkeypatch):
    monkeypatch.setattr(api, "_live_slots", set())
    monkeypatch.setattr(api.settings, "max_concurrent_streams", 3)


def test_a_channel_going_live_takes_a_slot(slots):
    assert api.acquire_live_slot("u:a") is True
    assert api.live_stream_count() == 1


def test_the_box_stops_handing_out_slots_at_the_ceiling(slots):
    for i in range(3):
        assert api.acquire_live_slot(f"u:{i}") is True
    assert api.acquire_live_slot("u:overflow") is False, \
        "a fourth live stream started on a box sized for three"
    assert api.live_stream_count() == 3


def test_a_refused_channel_takes_nothing(slots):
    """It has to be able to come back and ask again. If a refusal left it
    counted, the box would deadlock at the ceiling with workers waiting on
    slots they were themselves occupying."""
    for i in range(3):
        api.acquire_live_slot(f"u:{i}")
    api.acquire_live_slot("u:overflow")
    assert "u:overflow" not in api._live_slots


def test_asking_twice_does_not_take_two_slots(slots):
    """A worker that reconnects mid-session asks again. Counting that twice
    would leak a slot per reconnect."""
    api.acquire_live_slot("u:a")
    api.acquire_live_slot("u:a")
    assert api.live_stream_count() == 1


def test_releasing_frees_the_slot_for_somebody_else(slots):
    for i in range(3):
        api.acquire_live_slot(f"u:{i}")
    assert api.acquire_live_slot("u:next") is False
    api.release_live_slot("u:0")
    assert api.acquire_live_slot("u:next") is True


def test_releasing_a_slot_never_held_is_harmless(slots):
    """The worker releases unconditionally in a finally, and the common path —
    every ChannelOffline — never took one, because the slot is acquired after
    the liveness check. This must not raise."""
    api.release_live_slot("u:never-had-one")
    assert api.live_stream_count() == 0


def test_removing_a_stream_gives_its_slot_back(slots, monkeypatch):
    """Otherwise deleting a live channel leaks capacity until restart."""
    import inspect
    src = inspect.getsource(api)
    # Every place a stream is removed from the registry must also free a slot.
    deletes = src.count("del _streams[")
    releases = src.count("release_live_slot(")
    # -2 for the definition and the docstring reference in acquire.
    assert releases >= deletes, (
        f"{deletes} places delete a stream but only {releases} release a slot; "
        f"one of them leaks capacity until the process restarts")


def test_the_worker_takes_a_slot_only_after_the_channel_is_confirmed_live():
    """Asking earlier rations channels that are not running — the original bug.
    Asking later means the audio meter is already spawned."""
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker.StreamWorker._run_session)
    assert "acquire_live_slot" in src, "the worker no longer asks for a slot"
    assert src.index("get_stream_info") < src.index("acquire_live_slot"), \
        "the slot is taken before the channel is known to be live"
    assert src.index("acquire_live_slot") < src.index("AudioMeter"), \
        "the audio meter is spawned before a slot is granted"


def test_a_queued_channel_does_not_claim_to_be_offline():
    """A card reading "offline" for a channel that is plainly streaming is the
    lie the viewer is most likely to catch."""
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker.StreamWorker.start)
    assert "CapacityQueued" in src, "the queued case is not handled separately"
    assert '"queued"' in src, "a queued channel reports some other status"


def test_the_slot_is_released_even_when_a_session_explodes():
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker.StreamWorker.start)
    i = src.index("_run_session()")
    # To the first except, which is where the try/finally around the session
    # must have closed. A fixed character window straddles the comment block
    # and measures nothing.
    tail = src[i:src.index("except", i)]
    assert "finally:" in tail, "the session call has no finally"
    assert "release_live_slot" in tail, \
        "the release is not on the unconditional path"


# ── paused channels (prod, 2026-11-04) ──────────────────────────────────────
# 48 listed against a ceiling of 48, 29 of them paused by the idle reaper, so
# every add was refused with 503 and a customer who had just bought Pro could
# not add a channel. A paused channel runs nothing; it must not hold a slot.

def _pause(uid, n):
    for i in range(n):
        api._streams[f"{uid}:p{i}_{uid}"] = {"channel": f"p{i}_{uid}", "user_id": uid,
                                             "status": "paused"}


def test_paused_channels_do_not_fill_the_server(pool):
    """The prod shape scaled to cap 20: full on paper, mostly paused."""
    _pause("idle1", 6)
    _pause("idle2", 6)
    pool("active", 8)                    # 20 listed, 8 counted
    assert len(api._streams) == 20
    assert _refused("newpro") is None
    assert _refused("active") is None


def test_a_server_full_of_running_channels_still_refuses(pool):
    """The ceiling still means something when the channels are real."""
    pool("a", 10)
    pool("b", 10)
    _pause("idle", 5)
    assert _refused("newcomer") == 503


def test_paused_channels_do_not_make_their_owner_the_heavy_user(pool):
    """Fair share counts what is running. Someone whose channels are all
    paused is not the one to cut back, and does not dilute everyone's share."""
    pool("heavy", 9)
    pool("mid", 8)
    _pause("away", 10)
    pool("light", 1)                     # 18 of 20 counted: the pool is tight
    assert _refused("heavy") == 429      # fair = 20 // 3 users, not // 4
    assert _refused("light") is None
    assert api._counted_stream_keys().__len__() == 18


def test_resuming_a_paused_user_never_asks_the_capacity_check(pool, monkeypatch):
    """Returning users get their channels back even past the ceiling; only
    going live is rationed, by acquire_live_slot."""
    import asyncio
    pool("a", 10)
    pool("b", 10)
    _pause("back", 3)
    called = []
    monkeypatch.setattr(api, "_check_server_capacity", lambda uid: called.append(uid))
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_publish_new_stream", None)

    async def _bcast(msg, user_id=None):
        pass
    monkeypatch.setattr(api, "broadcast", _bcast)
    assert asyncio.run(api._resume_user_streams("back")) == 3
    assert called == []
    assert all(r["status"] == "starting" for k, r in api._streams.items() if k.startswith("back:"))


# ── The disk budgets follow the machine too (2026-11-13, the 16 GB resize) ────
# The live ceiling grows with the cores, and two budgets it leans on were typed
# for the old box: 4 GB for every live channel's rolling video buffer (a FUSE:
# past it capture stops, and a Kick clip is nothing but a cut from that buffer)
# and 15 GB of cut clips (past it the oldest unreviewed videos are deleted).
# A bigger ceiling with the same 4 GB blows the fuse on a healthy box.

from config.settings import (_CAPTURE_FLOOR_MB, _CAPTURE_MB_PER_LIVE,
                             _CLIP_STORE_FLOOR_MB, _disk_total_mb,
                             default_clip_capture_max_total_mb,
                             default_clip_file_max_total_mb)

_GB = 1024


def test_the_old_box_keeps_its_buffer_budget_and_a_bigger_one_gets_more():
    assert default_clip_capture_max_total_mb(12, 77 * _GB) == 4096
    big = default_clip_capture_max_total_mb(48, 320 * _GB)
    assert big == 48 * _CAPTURE_MB_PER_LIVE == 9600


def test_the_fuse_cannot_blow_with_every_live_slot_at_its_fattest():
    """The point of deriving it: at ANY ceiling the cores produce, every live
    channel can hold a full ring without the total reaching the fuse."""
    for cores in (1, 2, 4, 8, 16, 32):
        live = cores * _STREAMS_PER_CPU
        budget = default_clip_capture_max_total_mb(live, 320 * _GB)
        assert budget >= live * _CAPTURE_MB_PER_LIVE, (cores, budget)


def test_the_buffers_can_never_be_promised_most_of_the_disk():
    """400 live slots on the small disk would ask for 80 GB of a 77 GB disk."""
    budget = default_clip_capture_max_total_mb(400, 77 * _GB)
    assert budget <= 0.15 * 77 * _GB
    assert budget >= _CAPTURE_FLOOR_MB


def test_the_clip_store_is_a_share_of_the_disk():
    assert default_clip_file_max_total_mb(77 * _GB) == int(77 * _GB * 0.40)     # ~30.8 GB
    assert default_clip_file_max_total_mb(320 * _GB) == 128 * _GB


@pytest.mark.parametrize("live", [0, 6, 12, 48, 400])
@pytest.mark.parametrize("disk_gb", [0, 10, 25, 77, 320, 2000])
def test_no_box_ever_gets_less_than_it_had(live, disk_gb):
    """The old numbers are floors. A derivation that could LOWER a budget would
    delete videos, or stop capture, on a box that was working."""
    assert default_clip_capture_max_total_mb(live, disk_gb * _GB) >= 4096
    assert default_clip_file_max_total_mb(disk_gb * _GB) >= 15360


def test_a_disk_nobody_could_read_keeps_the_old_numbers():
    assert default_clip_capture_max_total_mb(48, 0) == _CAPTURE_FLOOR_MB
    assert default_clip_file_max_total_mb(0) == _CLIP_STORE_FLOOR_MB


def test_the_disk_is_found_for_a_folder_that_does_not_exist_yet(tmp_path):
    """First start: clips/ is created later. It is the same disk as its parent."""
    assert _disk_total_mb(str(tmp_path / "not" / "made" / "yet")) == _disk_total_mb(str(tmp_path)) > 0


def test_settings_derives_both_budgets_from_this_machine(monkeypatch):
    monkeypatch.delenv("CLIP_CAPTURE_MAX_TOTAL_MB", raising=False)
    monkeypatch.delenv("CLIP_FILE_MAX_TOTAL_MB", raising=False)
    monkeypatch.delenv("MAX_CONCURRENT_STREAMS", raising=False)
    monkeypatch.setattr("config.settings._usable_cpus", lambda: 8)
    monkeypatch.setattr("config.settings._disk_total_mb", lambda path: 320 * _GB)
    s = Settings()
    assert s.max_concurrent_streams == 48 and s.max_registered_streams == 192
    assert s.clip_capture_max_total_mb == 9600
    assert s.clip_file_max_total_mb == 128 * _GB


def test_the_buffer_budget_follows_a_pinned_ceiling_too(monkeypatch):
    """Pin 60 live in .env and the fuse has to cover 60, not what the cores say."""
    monkeypatch.delenv("CLIP_CAPTURE_MAX_TOTAL_MB", raising=False)
    monkeypatch.setenv("MAX_CONCURRENT_STREAMS", "60")
    monkeypatch.setattr("config.settings._disk_total_mb", lambda path: 320 * _GB)
    assert Settings().clip_capture_max_total_mb == 60 * _CAPTURE_MB_PER_LIVE


@pytest.mark.parametrize("pinned", ["777", "0"])
def test_a_budget_set_in_env_still_wins(monkeypatch, pinned):
    """0 included: it means "no cap" to the code that enforces these, and an
    owner who chose that must not have it replaced by a derived number."""
    monkeypatch.setattr("config.settings._disk_total_mb", lambda path: 320 * _GB)
    monkeypatch.setenv("CLIP_CAPTURE_MAX_TOTAL_MB", pinned)
    monkeypatch.setenv("CLIP_FILE_MAX_TOTAL_MB", pinned)
    s = Settings()
    assert s.clip_capture_max_total_mb == int(pinned)
    assert s.clip_file_max_total_mb == int(pinned)


def test_the_resolved_limits_reach_the_journal(monkeypatch):
    """The derivations log through the stdlib at import, before logging is set
    up, so those lines never appeared anywhere: "the resolved number is logged"
    was not true. The startup line is what a deploy check greps for."""
    import pathlib
    from config.settings import capacity_summary
    monkeypatch.delenv("MAX_CONCURRENT_STREAMS", raising=False)
    monkeypatch.setenv("CLIP_FILE_MAX_TOTAL_MB", "50000")
    monkeypatch.setattr("config.settings._usable_cpus", lambda: 8)
    monkeypatch.setattr("config.settings._disk_total_mb", lambda path: 320 * _GB)
    got = capacity_summary(Settings())
    assert got == {"cores": 8, "disk_mb": 320 * _GB, "live": 48, "listed": 192,
                   "capture_mb": 9600, "clip_store_mb": 50000,
                   "pinned": ["clip_file_max_total_mb"]}
    main_src = pathlib.Path("src/main.py").read_text()
    assert 'log.info("capacity_limits", **capacity_summary(settings))' in main_src
