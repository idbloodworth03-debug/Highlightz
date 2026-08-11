"""Automatic preset selection.

Presets were only ever picked by hand from a dropdown that defaults to
"Default", so the per-genre tuning in rules.py almost never reached the streams
it was written for. The group that failed hardest were small channels — thin
chat is precisely what the "small" preset compensates for, and nobody was
choosing it.

Twitch returns the category and the concurrent viewer count in the same lookup
we already make, so the pick can be made for the user.
"""

import pytest

from src.trigger.rules import (
    PRESETS, auto_preset, preset_for_game, SMALL_CHANNEL_VIEWERS,
)


def test_every_preset_it_can_return_actually_exists():
    """A typo here would silently fall back to default for a whole genre."""
    names = {p for _, p in __import__(
        "src.trigger.rules", fromlist=["GAME_PRESETS"]).GAME_PRESETS}
    missing = names - set(PRESETS)
    assert not missing, missing


def test_a_small_channel_gets_the_small_preset_whatever_it_is_playing():
    """Size beats genre. A 20-viewer Valorant stream has more in common with
    another 20-viewer stream than with a 20,000-viewer Valorant stream: the
    binding problem is three messages in a window, not that the game is fast."""
    for game in ("VALORANT", "Chess", "Just Chatting", "League of Legends"):
        assert auto_preset(game, 12) == "small", game


def test_a_big_channel_gets_its_genre():
    assert auto_preset("VALORANT", 4000) == "fps"
    assert auto_preset("League of Legends", 900) == "moba"
    assert auto_preset("Chess", 300) == "chess"
    assert auto_preset("Just Chatting", 600) == "variety"
    assert auto_preset("Slots", 250) == "casino"


def test_the_small_cutoff_is_applied_where_it_says_it_is():
    assert auto_preset("VALORANT", SMALL_CHANNEL_VIEWERS - 1) == "small"
    assert auto_preset("VALORANT", SMALL_CHANNEL_VIEWERS) == "fps"


def test_an_unknown_game_is_not_guessed_at():
    """An unfamiliar category is not evidence for any preset. Guessing would
    apply someone else's tuning to content it was never measured on."""
    assert auto_preset("Some Brand New Game", 800) == "default"
    assert preset_for_game("Some Brand New Game") is None


def test_an_offline_channel_falls_back_rather_than_being_called_small():
    """viewer_count is 0 when a channel is offline, which is not the same as
    tiny — treating it as "small" would hand every offline add the lowest
    threshold in the table."""
    assert auto_preset("", 0) == "default"
    assert auto_preset("VALORANT", 0) == "fps"


def test_category_matching_survives_twitch_renames():
    """Twitch renames categories (Counter-Strike: GO -> Counter-Strike 2, FIFA
    -> EA Sports FC). Substring matching is why an exact table does not rot."""
    assert preset_for_game("Counter-Strike 2") == "fps"
    assert preset_for_game("Counter-Strike: Global Offensive") == "fps"
    assert preset_for_game("counter-strike 2") == "fps", "must be case-insensitive"


def test_an_explicit_choice_is_never_overridden():
    """Auto-selection only fills in for the untouched default. Silently
    replacing a deliberate pick would make the dropdown a lie."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.add_stream)
    assert 'if preset == "default"' in src, \
        "auto-preset must only apply when the user left the dropdown alone"


def test_the_lookup_happens_outside_the_data_lock():
    """It makes a Twitch call. Awaiting a network round-trip while holding
    _data_lock stalls every other clip in the pipeline."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.add_stream)
    assert src.index("_auto_preset_for") < src.index("async with _data_lock")


@pytest.mark.asyncio
async def test_a_twitch_failure_still_lets_the_stream_be_added(monkeypatch):
    """Adding a stream must never fail because a nicety could not be computed."""
    from src.dashboard import api

    class _Boom:
        async def get_stream_info(self, channel):
            raise RuntimeError("twitch down")
        async def close(self):
            pass

    import src.ingestion.platform.twitch as tw
    monkeypatch.setattr(tw, "TwitchPlatform", _Boom)
    assert await api._auto_preset_for("anyone") == "default"


# ── the preset has to actually reach the scoring, not just be chosen ─────────
#
# The picker above was correct and tested from the day it shipped, and the
# feature still did nothing for small channels: the preset's trigger_threshold
# never reached the trigger decision. Testing the pure function proved the
# choice was right and said nothing about whether anything acted on it. These
# tests cover the wiring instead.

def test_a_profile_is_decayed_toward_its_own_preset_not_default(tmp_path, monkeypatch):
    """ProfileManager.load hardcoded get_rules(channel, "default"), so every
    load dragged the threshold toward 63 whatever preset the channel was on.
    The worker's hourly decay pulled toward the real seed, so the two fought —
    and since a deploy restarts the service and runs load(), the load side won.
    A "small" channel crept toward 50 while monitored and was yanked back to a
    big-channel bar on every deploy, which is why the preset never took hold.
    """
    import asyncio, json, time
    from src.profiles.manager import ProfileManager
    from src.profiles.profile import StreamerProfile

    pm = ProfileManager(user_id="u1")
    monkeypatch.setattr(pm, "_path", lambda ch: tmp_path / f"{ch}.json")
    monkeypatch.setattr(pm, "_seed_path", lambda ch: tmp_path / "nope.json")

    p = StreamerProfile(channel="nova")
    p.preset = "small"                       # seed 50
    p.trigger_threshold = 70.0               # pushed up by rejections
    p.last_decay_ts = time.time() - 40 * 3600
    (tmp_path / "nova.json").write_text(json.dumps(p.to_dict()))

    loaded = asyncio.run(pm.load("nova"))
    assert loaded.trigger_threshold < 70.0, "no decay happened at all"
    # Toward small's 50, not default's 63. Asserting the direction rather than a
    # figure: the rate is tuned elsewhere and this test is about the target.
    assert loaded.trigger_threshold < 63.0, (
        f"decayed to {loaded.trigger_threshold}, which is above default's seed — "
        f"the channel's own preset was ignored")


def test_the_preset_survives_a_round_trip_through_the_profile_file():
    """The seed is read from the profile on load, so it has to persist. If it
    silently dropped, every restart would fall back to default and the bug
    would come back wearing a different hat."""
    from src.profiles.profile import StreamerProfile
    p = StreamerProfile(channel="nova")
    p.preset = "irl"
    assert StreamerProfile.from_dict(p.to_dict()).preset == "irl"


def test_a_profile_written_before_presets_existed_still_loads():
    """Old profiles have no preset key. They must deserialise to "default" —
    which is what they were already being decayed toward — so upgrading moves
    nobody's threshold."""
    from src.profiles.profile import StreamerProfile
    old = StreamerProfile(channel="nova").to_dict()
    old.pop("preset")
    assert StreamerProfile.from_dict(old).preset == "default"


def test_fixing_the_decay_seed_can_only_loosen_never_tighten():
    """The blast radius, asserted rather than reasoned about. Every preset's
    seed is at or below default's, so pointing the decay at the real preset
    lowers the bar or leaves it alone on every channel — it cannot reduce clips
    anywhere. If someone adds a preset with a seed above default's, this fails
    and they have to say so on purpose."""
    from src.trigger.rules import PRESETS
    default_seed = PRESETS["default"].trigger_threshold
    for name, rules in PRESETS.items():
        assert rules.trigger_threshold <= default_seed, (
            f"preset {name} seeds at {rules.trigger_threshold}, above default's "
            f"{default_seed} — switching to it would RAISE the bar and cut clips")


def test_the_preset_is_not_seeded_onto_a_fresh_profile():
    """Deliberately NOT done. A new profile starts at 60 for everyone; seeding
    from the preset would move default 60 -> 63, fps -> 61 and moba -> 62, i.e.
    stricter, and "default" is the biggest population because it is the
    fallback for unknown games and offline-at-add channels. The obvious fix
    would have cut clips for most users."""
    from src.profiles.profile import StreamerProfile
    from src.trigger.rules import PRESETS
    fresh = StreamerProfile(channel="nova")
    assert fresh.trigger_threshold == 60.0
    assert fresh.trigger_threshold < PRESETS["default"].trigger_threshold


# ── re-resolving when the channel actually goes live ─────────────────────────

def _worker(preset="default", game="", viewers=0, profile=None):
    """A StreamWorker with just enough wired up to exercise _resolve_preset."""
    from src.ingestion.stream_worker import StreamWorker, WorkerConfig
    from src.ingestion.platform.base import StreamInfo
    from src.profiles.profile import StreamerProfile

    w = StreamWorker(config=WorkerConfig(channel="nova", platform_name="twitch",
                                         user_id="u1", preset=preset),
                     platform=None, queue=None, shared_buffers={})
    w._profile = profile if profile is not None else StreamerProfile(channel="nova")
    w._stream_info = StreamInfo(channel="nova", platform="twitch",
                                stream_url="", chat_channel_id="nova",
                                title="t", game=game, viewer_count=viewers)
    return w


@pytest.fixture
def quiet(monkeypatch):
    """Silence the profile save and the broadcast so _resolve_preset can run
    without a filesystem or a websocket."""
    from src.ingestion import stream_worker
    from src.dashboard import api

    saved = []

    class _PM:
        async def save(self, p): saved.append(p)

    # Patched on stream_worker, not on src.profiles.manager: the worker does
    # `from ... import get_profile_manager` at module load, so the name is
    # already bound and patching the source module would miss it.
    monkeypatch.setattr(stream_worker, "get_profile_manager", lambda uid: _PM())

    sent = []

    async def _bc(msg, **kw): sent.append(msg)
    monkeypatch.setattr(api, "broadcast", _bc)
    return saved, sent


def test_a_channel_added_while_offline_is_repicked_when_it_goes_live(quiet):
    """The whole reason this exists. get_stream_info raises for an offline
    channel, so POST /streams could only ever return "default" for one — and a
    clipper queueing up the afternoon's roster adds every channel offline. The
    pick was never revisited, so those streams ran on default forever."""
    import asyncio
    w = _worker(preset="default", game="VALORANT", viewers=20)
    asyncio.run(w._resolve_preset("u1:nova"))
    assert w._preset == "small"


def test_the_repick_reaches_the_engine_and_the_decay_not_just_a_variable(quiet):
    """Setting an attribute nobody reads is how the first version of this
    failed. Both consumers must read the resolved value, not the frozen config."""
    from pathlib import Path
    src = Path("src/ingestion/stream_worker.py").read_text()
    assert "preset=self._preset," in src, "the TriggerEngine still gets the config preset"
    assert "get_rules(self._config.channel, self._preset)" in src, \
        "the hourly threshold decay still uses the config preset"


def test_an_explicit_choice_is_still_never_overridden_at_go_live(quiet):
    """Same rule as POST /streams. A user who picked "chess" for a Just
    Chatting segment must keep it."""
    import asyncio
    w = _worker(preset="chess", game="VALORANT", viewers=20)
    asyncio.run(w._resolve_preset("u1:nova"))
    assert w._preset == "chess"


def test_the_repick_is_written_to_the_profile_so_later_loads_agree(quiet):
    """Otherwise the decay seed and the engine disagree the moment the worker
    stops — which is the original bug, rebuilt."""
    import asyncio
    saved, _ = quiet
    w = _worker(preset="default", game="chess", viewers=4000)
    asyncio.run(w._resolve_preset("u1:nova"))
    assert w._profile.preset == "chess"
    assert saved, "the profile was never persisted, so the next load loses it"


def test_the_repick_reaches_an_open_tab(quiet):
    """Realtime contract: the stream card shows the preset. Changing it
    server-side without telling the tab means it reads wrong until a refresh."""
    import asyncio
    from src.dashboard import api
    _, sent = quiet
    record = {"channel": "nova", "preset": "default", "user_id": "u1"}
    api._streams["u1:nova"] = record
    try:
        w = _worker(preset="default", game="Just Chatting", viewers=9000)
        asyncio.run(w._resolve_preset("u1:nova"))
        assert record["preset"] == "variety"
        assert any(m.get("event") == "stream_updated" for m in sent), \
            "no broadcast — an open tab keeps showing the old preset"
    finally:
        api._streams.pop("u1:nova", None)


def test_no_broadcast_when_nothing_changed(quiet):
    """A worker reconnects every 30s while a channel is offline and re-resolves
    on each session. Broadcasting an unchanged preset every time would be pure
    socket noise."""
    import asyncio
    _, sent = quiet
    w = _worker(preset="fps", game="VALORANT", viewers=9000)
    asyncio.run(w._resolve_preset("u1:nova"))
    assert not [m for m in sent if m.get("event") == "stream_updated"]


def test_every_resolution_is_logged_including_the_boring_one(quiet, capsys):
    """The reason this whole investigation needed source-reading instead of a
    journal query: the old logging fired only when the pick was NOT default, so
    the most common outcome left no trace and an empty journal looked identical
    to a feature that was never deployed."""
    import asyncio
    w = _worker(preset="default", game="Some Unknown Game", viewers=9000)
    asyncio.run(w._resolve_preset("u1:nova"))
    assert w._preset == "default"
    # capsys, not caplog: structlog is configured to write to stdout directly
    # and never reaches the stdlib logging handlers caplog installs.
    out = capsys.readouterr().out
    assert "preset_resolved" in out
    # The inputs, so a bad pick can be explained without reproducing it.
    assert "viewers=9000" in out and "preset=default" in out


def test_run_session_actually_calls_the_repick_before_building_the_engine(quiet, monkeypatch):
    """Deleting the one call in _run_session left every test above green,
    because they all invoke _resolve_preset directly. So drive the session
    instead, and abort it the moment the engine is constructed: reaching that
    line with the preset unresolved is the regression.

    Order matters as much as the call — TriggerEngine reads self._preset at
    construction, so resolving afterwards would build the engine on the stale
    value and only take effect on the next reconnect.
    """
    import asyncio
    from src.ingestion import stream_worker
    from src.ingestion.platform.base import StreamInfo

    class _Stop(Exception): pass

    def _boom(*a, **k): raise _Stop()
    monkeypatch.setattr(stream_worker, "TriggerEngine", _boom)
    monkeypatch.setattr(stream_worker.settings, "enable_audio_detection", False)

    w = _worker(preset="default", game="VALORANT", viewers=12)
    info = w._stream_info

    class _Plat:
        async def get_stream_info(self, ch): return info
    w._platform = _Plat()
    w._stream_info = None          # force it to come from the platform

    with pytest.raises(_Stop):
        asyncio.run(w._run_session())
    assert w._preset == "small", \
        "the engine was built before the preset was re-resolved"
