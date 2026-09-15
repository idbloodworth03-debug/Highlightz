"""Kick monitoring is live (owner, 2026-09-15): "get kick integrated as best
as we can right now using the same thing we have with twitch".

What "as best as we can" means, and what these pin:

  * liveness/title/category/viewers come from Kick's PUBLIC API with an app
    token when KICK_CLIENT_ID/SECRET are set, else from the site endpoint;
  * the chatroom id (Pusher) only exists on the site endpoint, is cached on
    disk once learned, and a channel still runs without it;
  * a Kick clip is FILE-ONLY: no Kick clip API, so the processor returns a
    pending record pointing at the channel and the worker's live-capture cut
    is the video — and with capture off, both POST /streams and the processor
    refuse instead of half-working;
  * Twitch-only signals (viewer clips / Highlight clips) never run for a Kick
    channel, which would otherwise look up a Twitch user of the same name;
  * the dashboard no longer blocks any tab on Kick, plays a file-only clip in
    the modal, and links out to the channel on Kick.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from src.ingestion.platform import kick as kick_mod
from src.ingestion.platform.base import ChannelOffline
# The signed-in TestClient with its own users file; shared by import, the way
# pytest allows, rather than copied.
from tests.test_new_user_journey import app  # noqa: F401


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(kick_mod, "_CHATROOMS_FILE", tmp_path / "kick_chatrooms.json")
    kick_mod._chatrooms.clear()
    kick_mod._chatrooms_loaded = False
    yield
    kick_mod._chatrooms.clear()
    kick_mod._chatrooms_loaded = False


class Scripted(kick_mod.KickPlatform):
    """A KickPlatform whose HTTP is a dict of url-fragment -> (status, body)."""

    def __init__(self, script):
        super().__init__()
        self.script = script
        self.calls = []

    async def _get_json(self, url, *, headers=None, params=None):
        self.calls.append(("GET", url, params, headers))
        for frag, resp in self.script.items():
            if frag in url:
                return resp
        raise AssertionError(f"unexpected GET {url}")

    async def _post_form(self, url, data):
        self.calls.append(("POST", url, data, None))
        for frag, resp in self.script.items():
            if frag in url:
                return resp
        raise AssertionError(f"unexpected POST {url}")


PUBLIC_LIVE = (200, {"data": [{"slug": "xqc", "broadcaster_user_id": 1,
                               "stream_title": "ranked — grind", "category": {"name": "Just Chatting"},
                               "stream": {"is_live": True, "viewer_count": 1234, "is_mature": False}}]})
PUBLIC_OFF = (200, {"data": [{"slug": "xqc", "stream": {"is_live": False, "viewer_count": 0},
                              "stream_title": "", "category": {}}]})
SITE = (200, {"chatroom": {"id": 668}, "livestream": {"session_title": "site title",
                                                     "categories": [{"name": "Slots"}],
                                                     "viewer_count": 77, "is_mature": True}})
TOKEN = (200, {"access_token": "APP", "expires_in": 3600})


def _configured(monkeypatch, on=True):
    from config.settings import settings
    monkeypatch.setattr(settings, "kick_client_id", "cid" if on else "")
    monkeypatch.setattr(settings, "kick_client_secret", "sec" if on else "")


# ── the platform ─────────────────────────────────────────────────────────────

def test_public_api_with_an_app_token_gives_liveness_and_the_site_gives_the_chatroom(monkeypatch):
    _configured(monkeypatch)
    p = Scripted({"id.kick.com/oauth/token": TOKEN, "api.kick.com/public/v1/channels": PUBLIC_LIVE,
                  "kick.com/api/v2/channels/xqc": SITE})
    info = _run(p.get_stream_info("XQC"))
    assert info.platform == "kick" and info.channel == "xqc"
    assert info.stream_url == "https://kick.com/xqc", "streamlink takes the page URL"
    assert info.chat_channel_id == "668"
    assert info.title == "ranked  grind" and info.game == "Just Chatting"
    assert info.viewer_count == 1234 and info.is_mature is False
    # Client-credentials, not a user grant.
    tok = [c for c in p.calls if c[0] == "POST"][0]
    assert tok[2]["grant_type"] == "client_credentials"
    # The public call carried the app token.
    pub = [c for c in p.calls if "public/v1/channels" in c[1]][0]
    assert pub[3]["Authorization"] == "Bearer APP" and pub[2] == {"slug": "xqc"}


def test_the_chatroom_id_is_cached_on_disk_and_not_asked_for_again(monkeypatch):
    _configured(monkeypatch)
    p = Scripted({"id.kick.com/oauth/token": TOKEN, "api.kick.com/public/v1/channels": PUBLIC_LIVE,
                  "kick.com/api/v2/channels/xqc": SITE})
    _run(p.get_stream_info("xqc"))
    assert json.loads(kick_mod._CHATROOMS_FILE.read_text())["xqc"] == "668"
    # A fresh process, and the site endpoint now refuses (Cloudflare): the
    # cached id still carries the channel.
    kick_mod._chatrooms.clear(); kick_mod._chatrooms_loaded = False
    p2 = Scripted({"id.kick.com/oauth/token": TOKEN, "api.kick.com/public/v1/channels": PUBLIC_LIVE,
                   "kick.com/api/v2/channels/xqc": (403, None)})
    info = _run(p2.get_stream_info("xqc"))
    assert info.chat_channel_id == "668"
    assert not [c for c in p2.calls if "api/v2/channels" in c[1]], "asked the site again"


def test_a_channel_runs_without_chat_when_the_site_endpoint_is_blocked(monkeypatch):
    _configured(monkeypatch)
    p = Scripted({"id.kick.com/oauth/token": TOKEN, "api.kick.com/public/v1/channels": PUBLIC_LIVE,
                  "kick.com/api/v2/channels/xqc": (403, None)})
    info = _run(p.get_stream_info("xqc"))
    assert info.chat_channel_id == "" and info.viewer_count == 1234


def test_not_live_is_channel_offline_not_an_error(monkeypatch):
    _configured(monkeypatch)
    p = Scripted({"id.kick.com/oauth/token": TOKEN, "api.kick.com/public/v1/channels": PUBLIC_OFF,
                  "kick.com/api/v2/channels/xqc": SITE})
    with pytest.raises(ChannelOffline):
        _run(p.get_stream_info("xqc"))
    assert _run(p.is_live("xqc")) is False
    # Unknown slug too, and the public API is asked exactly once per call —
    # ChannelOffline is not retried.
    p = Scripted({"id.kick.com/oauth/token": TOKEN, "api.kick.com/public/v1/channels": (200, {"data": []})})
    with pytest.raises(ChannelOffline):
        _run(p.get_stream_info("nobody"))
    assert len([c for c in p.calls if "public/v1/channels" in c[1]]) == 1


def test_without_app_credentials_the_site_endpoint_carries_liveness(monkeypatch):
    _configured(monkeypatch, on=False)
    p = Scripted({"kick.com/api/v2/channels/xqc": SITE})
    info = _run(p.get_stream_info("xqc"))
    assert info.title == "site title" and info.game == "Slots"
    assert info.viewer_count == 77 and info.is_mature is True and info.chat_channel_id == "668"
    assert not [c for c in p.calls if c[0] == "POST"], "minted an app token with no credentials"
    p = Scripted({"kick.com/api/v2/channels/xqc": (200, {"chatroom": {"id": 668}, "livestream": None})})
    with pytest.raises(ChannelOffline):
        _run(p.get_stream_info("xqc"))


def test_a_near_miss_slug_from_the_public_api_is_not_monitored(monkeypatch):
    _configured(monkeypatch)
    p = Scripted({"id.kick.com/oauth/token": TOKEN,
                  "api.kick.com/public/v1/channels": (200, {"data": [{"slug": "xqcow", "stream": {"is_live": True}}]})})
    with pytest.raises(ChannelOffline):
        _run(p.get_stream_info("xqc"))


def test_the_chat_monitor_does_not_loop_on_an_empty_chatroom():
    from src.chat.platform.kick_chat import KickChatMonitor
    calls = []

    async def on_msg(a, m): calls.append((a, m))
    m = KickChatMonitor("", on_msg)
    _run(m.run())                       # returns at once rather than reconnecting forever
    assert m._running is False and calls == []


# ── clips are files ──────────────────────────────────────────────────────────

def test_a_kick_clip_is_a_pending_file_only_record_pointing_at_the_channel(monkeypatch):
    from config.settings import settings
    from src.processor.clip_processor import ClipProcessor
    from src.queue.job_queue import ClipJob
    monkeypatch.setattr(settings, "clip_capture_enabled", True)
    job = ClipJob(channel="xqc", platform="kick", trigger_score=80.0, trigger_signals=[],
                  chat_snapshot=[], stream_title="t", game="g", pre_roll=20, post_roll=0,
                  user_id="u1")
    meta = _run(ClipProcessor().process(job))
    assert meta.status == "pending" and meta.platform == "kick"
    assert meta.twitch_url == "" and meta.embed_url == ""
    assert meta.platform_url == "https://kick.com/xqc"
    assert meta.duration_seconds == 20.0
    assert meta.to_dict()["platform_url"] == "https://kick.com/xqc", "the card's link never reaches the record"


def test_adding_a_kick_channel_needs_capture_and_then_works(app, monkeypatch):
    from config.settings import settings
    app.onboard()
    monkeypatch.setattr(settings, "clip_capture_enabled", False)
    r = app.post("/streams", json={"channel": "xqc", "platform": "kick", "preset": "default"})
    assert r.status_code == 503 and "capture" in r.json()["detail"].lower()
    monkeypatch.setattr(settings, "clip_capture_enabled", True)
    r = app.post("/streams", json={"channel": "xqc", "platform": "kick", "preset": "default"})
    assert r.status_code == 201, r.text
    assert r.json()["platform"] == "kick"


def test_the_worker_no_longer_skips_kick_and_never_polls_helix_for_it():
    import inspect
    from src import main
    from src.ingestion import stream_worker
    assert "kick_worker_skipped" not in inspect.getsource(main.spawn_worker)
    src = inspect.getsource(stream_worker.StreamWorker._record_viewer_clips)
    assert 'if self._config.platform_name != "twitch":' in src
    assert "return" in src.split('!= "twitch":')[1][:40]


def test_a_manual_clip_on_kick_is_only_refused_while_capture_is_off():
    import inspect
    from src import main
    src = inspect.getsource(main.main)
    assert 'platform == "kick" and not settings.clip_capture_enabled' in src
    assert "Manual clips are only available on Twitch" not in src


# ── the dashboard ────────────────────────────────────────────────────────────

def test_no_tab_is_blocked_on_kick_and_the_mechanism_survives():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    assert "const KICK_BLOCKED=[];" in h
    assert "activePlatform==='kick' && KICK_BLOCKED.includes(n.id)" in h, "the gate mechanism was removed"
    assert "Coming soon</span>" not in h[h.index("{/* Kick row */}"):h.index("{/* Legal links */}")]


def test_a_file_only_clip_plays_in_the_modal_and_links_to_kick():
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    modal = h[h.index("function ClipModal("):h.index("function ", h.index("function ClipModal(") + 10)]
    assert "const fileSrc = (!embedSrc && clip.has_file) ? '/clips/' + clip.id + '/file' : ''" in modal
    assert "<video src={fileSrc}" in modal
    assert "const outHref = twHref || clip.platform_url || ''" in modal
    assert "Open channel on Kick" in modal
    card = h[h.index("function RdClip("):h.index("function ClipModal(")]
    assert "Open on {outName}" in card and "clip.platform === 'kick' ? 'Kick' : 'Twitch'" in card


def test_the_legal_pages_and_the_brief_say_what_kick_actually_is(app):
    app.cookies.clear()
    for path in ("/tos", "/privacy"):
        t = app.get(path).text.lower()
        assert "kick support is not" not in t, f"{path} still says Kick is not live"
        assert "no kick credentials are requested or stored" in t
    assert "no clip is created or hosted on kick" in app.get("/tos").text.lower()
    assert "recorded from that live public broadcast" in app.get("/privacy").text.lower()
    brief = app.get("/llms.txt").text.lower()
    assert "kick support is not live" not in brief
    assert "kick" in brief and "highlight clips are twitch-only" in brief
