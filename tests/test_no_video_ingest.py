"""Where video may and may not be held.

For most of the product's life this file pinned a simple claim: no stream
video, ever. Clips were created through Helix on the user's own token, Twitch
hosted every byte, and the only pull was an audio-only feed into a loudness
meter that wrote nothing to disk.

THAT CHANGED DELIBERATELY (2026-09-08, owner's decision). The product now
records a short rolling buffer of each monitored channel so a clip can be a
FILE the user downloads, edits and schedules without leaving the site — see
src/ingestion/clip_recorder.py. So these tests no longer assert the absence of
video. They assert the SHAPE of the thing that replaced it, which is the part
that can rot quietly:

  * exactly one module is allowed to pull video, and everything else that
    touches streamlink still asks for audio only;
  * that module never transcodes, because clip detection shares the core;
  * the audio meter still holds nothing on disk;
  * capture is off unless switched on, and refuses an opted-out broadcaster
    (the ordering of that check is pinned in tests/test_clip_capture.py);
  * we still create clips through the official API rather than assembling
    them ourselves, and still do not rewrite thumbnail URLs into media URLs.

THE SECOND REVERSAL (2026-09-15, owner's decision). Holding video made "and we
also fetch from Twitch" the obvious next step, and for two months it was the
step deliberately not taken. Then the owner took it, with the risk stated to
them plainly: clips the recorder missed, and every clip from before capture
existed, could never be downloaded, edited or scheduled, and that was the
product's whole promise. So `src/clips/fetch.py` now retrieves a clip's video
from Twitch through streamlink — the same playback-token path every clip tool
uses — when, and only when, capture produced nothing. Capture stays primary.

What these tests guard changed shape again rather than going away: there are
now exactly TWO modules that pull video, the fetcher honours the opt-out, it
is off by default, and the thumbnail-rewrite and hand-rolled-downloader bans
still stand — the point of going through streamlink is that there is no
undocumented URL in this codebase for Twitch to change out from under us.
"""

import pathlib
import re

SRC = pathlib.Path(__file__).resolve().parent.parent / "src"


def _python_sources():
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in str(p)]


def _code(path: pathlib.Path) -> str:
    """Source with # comment lines removed.

    Every scan here looks for the NAME of something forbidden, and the comments
    explaining why it is forbidden contain that name. Without this, a note
    saying "we deliberately do not shell out to yt-dlp" fails the test that
    checks nobody shells out to yt-dlp.
    """
    return "\n".join(l for l in path.read_text().splitlines()
                     if not l.lstrip().startswith("#"))


def test_no_video_buffering_module_exists():
    """Deleted deliberately. If it comes back, the no-re-hosting claim needs
    re-examining rather than the test being updated."""
    assert not (SRC / "ingestion" / "video_buffer.py").exists()
    # Strip comments first: the annotation that used to name the deleted class
    # is now a comment explaining why it went, and would satisfy this by itself.
    needle = "Video" + "Buffer"
    offenders = [p.relative_to(SRC) for p in _python_sources()
                 if needle in _code(p)]
    assert offenders == [], f"a video buffer is referenced again: {offenders}"


# The modules allowed to pull video. Adding one is the change this guard
# exists to make visible: every extra pull is another multiple of the
# bandwidth bill and another place the opt-out has to be re-checked.
#
# TWO SINCE 2026-09-15. The recorder captures the live broadcast; the fetcher
# retrieves a clip's video from Twitch when the recorder produced nothing —
# the owner's decision, with the risk in front of them (see docs/HANDOFF.md,
# "Non-negotiable product constraints"). The fetcher's own tests pin that it
# honours the opt-out, runs one download at a time and is off by default.
_VIDEO_PULLER = "ingestion/clip_recorder.py"
_CLIP_FETCHER = "clips/fetch.py"
_VIDEO_PULLERS = {_VIDEO_PULLER, _CLIP_FETCHER}


def test_only_the_named_modules_pull_video_and_everything_else_asks_for_audio():
    """streamlink is how the engine hears the stream, how it sees it, and now
    how it retrieves a clip. The split has to stay explicit: the meter asks
    for `audio_only,worst`, and the two named modules are the exceptions.

    Comments are stripped first. The recorder's own docstring explains what
    the meter asks for, and matching that string would let this pass on a file
    that pulls full video — which is the exact false green this replaced.
    """
    callers = {str(p.relative_to(SRC)) for p in _python_sources()
               if "streamlink_path" in _code(p)}
    assert callers, "no streamlink caller found — did the meter move?"
    assert _VIDEO_PULLER in callers, \
        "the clip recorder no longer pulls the stream — has capture been removed?"
    assert _CLIP_FETCHER in callers, \
        "the clip fetcher no longer uses streamlink — what is it downloading with?"
    for rel in callers - _VIDEO_PULLERS:
        src = _code(SRC / rel)
        assert "audio_only" in src, \
            f"{rel} pulls a stream without requesting audio_only"


def test_the_fetcher_checks_the_opt_out_before_it_fetches():
    """The recorder refuses an opted-out channel before it starts; a fetched
    file is the same thing to that broadcaster. Pinned here beside the puller
    list so adding a puller and forgetting the opt-out fail in the same place."""
    src = _code(SRC / _CLIP_FETCHER)
    assert "is_opted_out(" in src, "the fetcher does not consult the opt-out list"


def test_the_recorder_never_transcodes():
    """The whole cost argument for capture is that ffmpeg only remuxes what
    the platform already encoded. An encoder here would compete with the audio
    meter for the single core, and clip detection must always win."""
    src = _code(SRC / _VIDEO_PULLER)
    assert '"-c", "copy"' in src, "the recorder is no longer stream-copying"
    for encoder in ("libx264", "libx265", "-crf", "-preset"):
        assert encoder not in src, f"the recorder invokes an encoder ({encoder})"


def test_capture_is_off_unless_it_is_switched_on():
    """Same shape as uploads: a feature that holds bytes does not arrive
    switched on by a deploy."""
    from config.settings import Settings
    assert Settings().clip_capture_enabled is False


def test_the_buffer_is_bounded_by_a_disk_cap():
    """A ring with no ceiling is an archive that has not filled up yet. The
    disk is shared with the clip store, the user database and billing."""
    from config.settings import Settings
    s = Settings()
    assert s.clip_capture_max_total_mb > 0
    assert s.clip_capture_buffer_s > 0
    src = _code(SRC / _VIDEO_PULLER)
    assert "clip_capture_max_total_mb" in src, "nothing enforces the global cap"


def test_the_decoder_discards_video():
    """-vn is the belt to audio_only's braces: when streamlink falls back to
    `worst` it hands over a video stream, and this is what stops any frame
    being decoded, let alone written."""
    meter = (SRC / "ingestion" / "audio_meter.py").read_text()
    assert '"-vn"' in meter, "ffmpeg is no longer discarding the video track"
    assert '"s16le"' in meter, "the meter is no longer decoding to raw PCM"


def test_the_meter_writes_no_media_to_disk():
    """The whole posture depends on the audio being transient. A file handle
    here would turn a loudness probe into a recording."""
    meter = (SRC / "ingestion" / "audio_meter.py").read_text()
    for forbidden in ("open(", "NamedTemporaryFile", "mkstemp", ".write_bytes("):
        assert forbidden not in meter, \
            f"the audio meter now uses {forbidden} — it may be persisting media"
    # stdout=PIPE into memory is the intended shape; a file target is not.
    assert "stdout=asyncio.subprocess.PIPE" in meter or "stdout=w_fd" in meter


def test_clips_are_created_through_the_official_api():
    """The other half of the claim: the clip is made by Twitch, on the user's
    own account, not assembled by us."""
    clips = (SRC / "output" / "twitch_clips.py").read_text()
    assert "helix" in clips.lower(), "clip creation no longer goes through Helix"
    assert "user_token" in clips, \
        "clips are no longer created with the user's own token"


# The thumbnail-to-MP4 rewrite lives in exactly one file: a read-only probe run
# by hand (`python -m src.maintenance.probe_clip_media`) to answer whether a
# path to the file exists at all. It sends HEAD requests, stores nothing, and is
# imported by no serving code. Keeping it allowlisted rather than deleted is
# deliberate — it documents a decision — but it must never become reachable from
# a request.
_PROBE = "maintenance/probe_clip_media.py"

_SERVING_DIRS = ("dashboard", "ingestion", "output", "processor", "trigger",
                 "vod", "uploads", "publish")


def test_the_thumbnail_to_mp4_rewrite_is_not_in_any_serving_path():
    """This is the technique that gets tools their API access pulled: Twitch
    thumbnails follow a predictable pattern that can be rewritten into a direct
    MP4 URL for video Twitch offers through no API. It is fine to have measured
    whether it works; it is not fine for a request to be able to reach it."""
    for path in _python_sources():
        rel = str(path.relative_to(SRC))
        if rel == _PROBE or not rel.startswith(_SERVING_DIRS):
            continue
        # Per LINE, not per file. The dashboard legitimately rewrites
        # `-preview-480x270.jpg` into `-preview-1280x720.jpg` for a crisper
        # thumbnail — JPEG to JPEG. Matching both strings anywhere in a
        # five-thousand-line file flags that as media scraping, which it is not.
        # What is forbidden is a preview URL being turned into a video one.
        lines = _code(path).splitlines()
        for n, line in enumerate(lines):
            if "-preview-" not in line:
                continue
            window = " ".join(lines[max(0, n - 1):n + 2])
            assert ".mp4" not in window, \
                f"{rel}:{n + 1} rewrites a thumbnail URL into media"


def test_no_serving_code_shells_out_to_a_video_downloader():
    for path in _python_sources():
        src = _code(path)
        for tool in ("TwitchDownloader", "yt-dlp", "youtube-dl"):
            assert tool not in src, f"{path.relative_to(SRC)} shells out to {tool}"


def test_the_probe_remains_read_only():
    """Its whole defence is that it observes and changes nothing. A GET body or
    a file write would turn a measurement into a download."""
    src = _code(SRC / "maintenance" / "probe_clip_media.py")
    assert ".head(" in src, "the probe no longer uses HEAD"
    for writing in (".write_bytes(", ".write(", "open("):
        assert writing not in src, f"the probe now uses {writing} — it may be saving media"


# Twitch's private GraphQL endpoint, called with the web client's own client id.
# It is how every public VOD tool reads chat replay, because Helix offers no
# endpoint for it — but it is undocumented and unsanctioned, so its use is
# confined to ONE file and pinned here. This is narrower than the blanket "not
# in this product" the project notes claim, and the discrepancy is deliberate
# and known rather than an oversight.
_GQL_ALLOWED = {"vod/analyzer.py"}


def test_private_gql_is_confined_to_vod_chat_replay():
    """A new caller is the thing to catch. Chat replay is a bounded, video-free
    use; the same endpoint also serves playback tokens, and reaching for those
    would cross from 'undocumented' into 'downloading the video'."""
    users = {str(p.relative_to(SRC)) for p in _python_sources()
             if "gql.twitch.tv" in _code(p)}
    assert users <= _GQL_ALLOWED, \
        f"private GQL is now called from {users - _GQL_ALLOWED}"


def test_the_gql_call_asks_for_chat_and_not_for_video():
    src = (SRC / "vod" / "analyzer.py").read_text()
    assert "contentOffsetSeconds" in src, "the GQL query is no longer chat replay"
    for video_field in ("PlaybackAccessToken", "videoPlaybackAccessToken",
                        "signature", "streamPlaybackAccessToken"):
        assert video_field not in src, \
            f"the VOD analyzer is requesting {video_field} — that is video access"
