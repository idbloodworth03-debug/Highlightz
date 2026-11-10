"""A Kick clip with no file is never sent to Kick (2026-11-10).

sexylexy____ (Pro), on r2m_world, a Kick channel flagged mature: "I don't
understand the purpose of it notifying you about a clip of when you try to
open and play it, it says it's not available and to watch it on kick. Then
you go on kick and it's not there either." On prod every one of that
channel's 19 clips had its file. What she saw was the minute between the clip
being announced and its video being cut: with no file yet, and the mature
flag set, the modal showed the TWITCH gated banner ("it plays on Kick. Watch
on Kick") and made the whole player a link to the Kick channel page, where
the clip has never existed. A Kick clip is only the file Highlightz cut.

So: on Kick the gated banner never shows, nothing links out while there is
no file, and the modal says what is true: the video is being cut, or it is
gone. clip_file_ready already flips the open modal to the video live.
"""

import re

from src.dashboard.aurora_html import DASHBOARD_HTML


def _modal() -> str:
    i = DASHBOARD_HTML.index("function ClipModal(")
    j = DASHBOARD_HTML.index("\nfunction ", i + 10)
    return DASHBOARD_HTML[i:j]


def test_a_kick_clip_never_links_out_without_a_file():
    m = _modal()
    assert "const kick = clip.platform === 'kick';" in m
    assert "const canLinkOut = !embedSrc && !fileSrc && !!outHref && !kick;" in m
    # The play rings only appear where tapping opens something.
    assert m.count('{canLinkOut&&<div className="rd-modal-play">') == 2
    assert '{outHref&&<div className="rd-modal-play">' not in m


def test_the_gated_banner_is_twitch_only():
    m = _modal()
    assert "gated && !kick && !fileSrc && <div" in m
    # The Kick line comes first, so a mature-flagged Kick clip with no file
    # reads the Kick message and never the Twitch one.
    assert m.index("kick && !fileSrc && <div") < m.index("gated && !kick && !fileSrc && <div")


def test_a_kick_clip_without_a_file_says_cutting_or_gone():
    m = _modal()
    assert "Cutting the video now." in m and "No need to refresh." in m
    assert "No video for this clip." in m
    assert "so it is not on Kick either" in m
    gone = m[m.index("This clip's video is gone."):]
    gone = gone[:gone.index("This video was cleared.")]
    assert "only as the file" in gone and "unaffected" not in gone, \
        "on Kick the file IS the clip; 'the clip itself is unaffected' is false"


def test_the_pending_state_is_what_the_server_sends_in_that_window(monkeypatch):
    """The modal keys on file_state === 'pending', which _file_state returns
    for a clip younger than the cut wait with no file yet (capture on, as it
    is on prod)."""
    from src.dashboard import api
    import time
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    clip = {"id": "zz", "platform": "kick", "created_at": time.time() - 5}
    assert api._file_state(clip, listing=frozenset()) == "pending"
    assert api._CLIP_FILE_WAIT_S >= 60
