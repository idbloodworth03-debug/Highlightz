"""Age-restricted clips: taken, but never shown in a player that cannot play them.

THE REPORTED BUG was clips that appear in the queue with a dead player. They are
not broken clips — they are real, and they play fine on Twitch. What fails is our
EMBED: Twitch gates mature content behind an age confirmation, and inside a
third-party iframe it cannot make one, because the viewer's Twitch session is a
third-party cookie that browsers block. The player has no way to know who is
watching, so it refuses.

THE ASK WAS NOT "skip these channels". It was: take the clips, and make sure they
can be watched. So nothing is skipped and no clip is discarded — the dashboard
just stops rendering a frame that was always going to sit there black, and sends
the viewer to the one place the clip does play.

DETECTION IS FREE. Helix returns is_mature on every Get Streams response, which
the worker already calls for liveness. No extra request, nothing against the
shared rate limit.
"""

import dataclasses
import re

import pytest

from src.ingestion.platform.base import StreamInfo
from src.processor.metadata import ClipMetadata
from src.queue.job_queue import ClipJob


def _fn(name: str) -> str:
    """One component's source out of the dashboard bundle.

    Brace-matching from the `function` keyword does NOT work here: the props are
    destructured, so `({ clip, onOpen })` opens and closes a brace before the
    body starts and a naive matcher returns the signature alone. Same regex the
    suggested-clip tests use — the component ends at a `}` in column zero.
    """
    from src.dashboard.aurora_html import DASHBOARD_HTML
    m = re.search(r"function " + name + r"\(.*?\n\}\n\n", DASHBOARD_HTML, re.S)
    assert m, name + " not found"
    return m.group(0)


def _code(src: str) -> str:
    """Strip comments — the file explains this change at length and matching the
    explanation instead of the code would make every assertion below vacuous."""
    src = re.sub(r"\{/\*.*?\*/\}", "", src, flags=re.S)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


# ── detection, end to end and free ───────────────────────────────────────────

def test_stream_info_carries_the_mature_flag():
    assert "is_mature" in {f.name for f in dataclasses.fields(StreamInfo)}
    assert StreamInfo(channel="c", platform="twitch", stream_url="",
                      chat_channel_id="c").is_mature is False


def test_it_is_read_from_the_liveness_call_we_already_make():
    """The whole point is that it costs nothing. If this ever moves to its own
    endpoint it becomes a Helix call per stream, on a budget shared with clip
    creation."""
    import inspect
    from src.ingestion.platform import twitch
    src = inspect.getsource(twitch)
    assert 'is_mature=bool(stream.get("is_mature", False))' in src, \
        "the mature flag is no longer read off the Get Streams response"
    assert src.count("HELIX_BASE}/streams") >= 1
    assert "HELIX_BASE}/channels" not in src, \
        "a second Helix call was added to get something /streams already returns"


def test_the_flag_reaches_the_clip_record():
    assert "age_restricted" in {f.name for f in dataclasses.fields(ClipJob)}
    assert "age_restricted" in {f.name for f in dataclasses.fields(ClipMetadata)}
    assert ClipMetadata(age_restricted=True).to_dict()["age_restricted"] is True
    assert ClipMetadata().to_dict()["age_restricted"] is False


def test_both_kinds_of_clip_carry_it():
    """A crowd suggestion comes off the same channel and is gated identically,
    so it cannot be left out — the two land through different code paths."""
    import inspect
    from src.ingestion import stream_worker
    src = _code(inspect.getsource(stream_worker))
    assert src.count("age_restricted=bool(info.is_mature) if info else False") == 2, \
        "only one of the triggered and suggested paths sets the flag"


# ── the player ───────────────────────────────────────────────────────────────

def test_a_gated_clip_gets_no_iframe():
    """THE FIX. Rendering the embed for a gated clip produces a black box with a
    play button that can never work, which is what was reported."""
    body = _code(_fn("ClipModal"))
    assert "const gated = !!clip.age_restricted;" in body, \
        "the modal does not know whether the clip is gated"
    assert "const embedSrc = (embed && !gated)" in body, \
        "a gated clip is still given an embed source"


def test_a_gated_clip_opens_on_twitch_instead():
    body = _code(_fn("ClipModal"))
    assert "canLinkOut = !embedSrc" in body, \
        "with no embed the media area no longer becomes a link out"
    assert "Age-restricted on {outName}" in body, "the reason is never stated"
    assert "Watch on {outName}" in body, "there is no way to actually watch it"
    # And only when the flag really stops playback: a Kick clip is a captured
    # file that plays here, and "plays on Twitch" under it was a bug.
    assert "gated && !fileSrc && <div" in body, \
        "the age-restricted banner shows over a clip that is playing right there"


def test_the_gated_notice_does_not_claim_something_is_broken():
    """It is not an error. The clip is fine and so is the account — saying
    'player showing an error' here would send people looking for a fault that
    does not exist."""
    body = _code(_fn("ClipModal"))
    gated_bar = body[body.index("Age-restricted on {outName}"):]
    gated_bar = gated_bar[:gated_bar.index("Player showing an error?")]
    assert "error" not in gated_bar.lower()
    assert "plays on {outName}" in gated_bar


def test_the_card_says_so_before_you_open_it():
    """It changes what you do: a gated clip cannot be reviewed inline, and on a
    plan with a weekly keep limit it is worth knowing before spending a slot."""
    body = _code(_fn("RdClip"))
    assert "clip.age_restricted" in body, "the card never checks the flag"
    assert "Age-restricted" in body
    from src.dashboard.aurora_html import DASHBOARD_HTML
    assert ".rd-agebadge{" in DASHBOARD_HTML, "the badge has no styling"


@pytest.mark.parametrize("player", ["ClipModal", "TrainingScreen", "VodScreen"])
def test_every_player_asks_for_the_permissions_playback_needs(player):
    """Missing `encrypted-media` blocks EME, which Twitch playback can need. The
    landing page hero set this from the start; none of the dashboard players
    did, which is its own reason a frame can come up dead."""
    from src.dashboard.aurora_html import DASHBOARD_HTML
    frames = re.findall(r"<iframe[^>]*>", DASHBOARD_HTML)
    assert frames, "no iframes found — this test has stopped testing anything"
    for f in frames:
        if "embed" in f or "embedSrc" in f:
            assert "encrypted-media" in f, f"a player lacks encrypted-media: {f[:90]}"


# ── nothing is skipped ───────────────────────────────────────────────────────

def test_a_mature_channel_is_still_monitored_and_still_clipped():
    """The requirement was to KEEP taking these clips. A flag that quietly
    started skipping channels would be the opposite of what was asked."""
    import inspect
    from src.ingestion import stream_worker
    from src.processor import clip_processor
    for mod in (stream_worker, clip_processor):
        src = _code(inspect.getsource(mod))
        assert not re.search(r"if\s+.*is_mature.*:\s*\n\s*(return|continue)", src), \
            f"{mod.__name__} skips work when the channel is mature"
        assert not re.search(r"if\s+.*age_restricted.*:\s*\n\s*(return|continue)", src), \
            f"{mod.__name__} skips work when the clip is age-restricted"
