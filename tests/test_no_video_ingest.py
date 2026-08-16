"""The compliance line: we never hold stream video.

This is the product's stated position — clips are created through the official
Helix API with the user's own token and stay native Twitch clips, and nothing
re-hosts or re-encodes anyone's stream. It is also the claim on the comparison
page ("clip stays a native Twitch clip", "clips are not on a storage timer"),
so it is a marketing claim as well as an architectural one.

Claims like that decay quietly. src/ingestion/video_buffer.py sat in the tree
for a long time resolving HLS URLs and buffering video; nothing ever constructed
it, but TriggerEngine's `buffer` parameter was annotated "VideoBuffer | None",
so by reading alone the product looked like it buffered stream video. Anyone
auditing the claim — Twitch, a competitor, a journalist — would have found that
first and been right to ask.

These tests pin the mechanism rather than the intention.
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


def test_the_only_stream_pull_asks_for_audio():
    """streamlink is how the trigger engine hears the stream at all, so this is
    not "never touch the feed" — it is "never ask for the video track".

    `audio_only,worst` means audio when the platform offers an audio rendition
    and the lowest-quality stream when it does not, which is why the ffmpeg
    test below matters as much as this one.
    """
    callers = [p for p in _python_sources()
               if "streamlink_path" in p.read_text()]
    assert callers, "no streamlink caller found — did the meter move?"
    for path in callers:
        src = path.read_text()
        assert "audio_only" in src, \
            f"{path.relative_to(SRC)} pulls a stream without requesting audio_only"


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
