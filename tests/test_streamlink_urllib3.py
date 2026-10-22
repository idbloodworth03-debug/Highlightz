"""streamlink must work with the urllib3 it is installed beside (2026-10-22).

WHAT HAPPENED. The security upgrade moved urllib3 2.7.0 → 2.8.0. streamlink
8.4.0 replaces urllib3's internal `_PERCENT_RE` with a stand-in class that
only implements `subn`, the one method urllib3 2.x had called on it. 2.8.0
also calls `.sub` while normalizing a host name, so every URL with a host
name failed to parse inside streamlink:

    error: Unable to open URL: https://...playback.live-video.net/...m3u8
    (type object 'Urllib3UtilUrlPercentReOverride' has no attribute 'sub')

Every process that runs streamlink died: each live channel's clip recorder
(no video files; Kick clipping depends on it entirely), the audio meter
(the detector lost its audio signal), clip fetches and VOD audio. Twitch
clip creation kept going because it calls the API through aiohttp. The full
suite and a browser pass were green, because nothing tested streamlink
against a host name. These do, through the same patched session the CLI uses.
"""

import http.server
import threading

import pytest

pytest.importorskip("streamlink")

import streamlink                                   # noqa: E402
import streamlink.session.http                      # noqa: E402,F401  applies the patch
from urllib3.util.url import parse_url              # noqa: E402

PLAYLIST = b"#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\nseg0.ts\n#EXT-X-ENDLIST\n"


def test_a_stream_url_with_a_host_name_parses_under_streamlinks_patch():
    """The exact shape that failed: an IVS host and a percent-encoded token."""
    url = ("https://fa723fc1b171.us-west-2.playback.live-video.net/api/video/v1/"
           "us-west-2.196233775518.channel.XFe3ftHjLy1x.m3u8?token=abc%2Fdef%3D")
    u = parse_url(url)
    assert u.host == "fa723fc1b171.us-west-2.playback.live-video.net"
    # The point of streamlink's patch, which must keep working: percent-escapes
    # are passed through as given, not re-cased.
    assert "abc%2Fdef%3D" in u.query


@pytest.fixture
def playlist_server():
    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(PLAYLIST)))
            self.end_headers()
            self.wfile.write(PLAYLIST)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()


def test_streamlinks_own_session_fetches_a_playlist_by_host_name(playlist_server):
    """Through requests and urllib3 exactly as the recorder's streamlink does,
    by name ("localhost"), which is the path that broke."""
    s = streamlink.Streamlink()
    r = s.http.get(f"http://localhost:{playlist_server}/live/index.m3u8?token=a%2Fb", timeout=5)
    assert r.status_code == 200 and r.content == PLAYLIST
