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
