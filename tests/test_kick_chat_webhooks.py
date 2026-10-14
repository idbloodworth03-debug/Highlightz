"""Kick chat through Kick's signed event webhooks (owner, 2026-10-14).

From a Pro customer's second ticket: "Highlightz Pro is still not generating
clips from the Kick livestreams I'm monitoring". Production showed Kick chat
dead on EVERY channel: the Pusher key the old monitor used is refused on every
cluster (4001 "not in this cluster", 459 reconnects on one channel in a day),
so Kick channels were scored on audio and viewers alone. Chat now comes the
sanctioned way — an app-token subscription per channel, and Kick POSTing each
message to /kick/webhook signed with its RSA key.

What these pin:
  1. a correctly signed delivery reaches the session listening for that
     channel — through the real endpoint, signed out, verified on the raw body;
  2. nothing unsigned reaches anyone: a tampered body, another key, missing
     headers; a rotated key is re-fetched once, and a stream of forgeries
     cannot turn into a stream of requests to Kick;
  3. every signed delivery is answered 200, even one nobody listens to (Kick
     unsubscribes a webhook that keeps failing);
  4. one chat message delivered twice is counted once;
  5. an emote tag reads as the emote's name, so hype-emote weighting applies;
  6. subscribing sends the documented body with the app token, is recorded on
     disk and not repeated while recorded; a renewal is rate-limited and
     creates before it deletes; a refusal is logged and retried later;
  7. a channel nobody lists any more is unsubscribed, a listed one is not, and
     the pruning task cannot take the server down;
  8. the monitor: chat reaches the engine, a quiet live chat is renewed, and a
     channel with no broadcaster id waits instead of ending its session;
  9. the platform hands the worker Kick's broadcaster id, from the public API
     or the site endpoint.
"""

from __future__ import annotations

import asyncio
import base64
import json

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from src.chat.platform import kick_events as ke
from src.chat.platform.kick_chat import KickChatMonitor

KICK_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
FORGER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
REAL_APP_TOKEN = ke._app_token


def _pem(priv, fmt=serialization.PublicFormat.SubjectPublicKeyInfo) -> str:
    return priv.public_key().public_bytes(serialization.Encoding.PEM, fmt).decode()


def _chat(bid=1001, user="viewer1", text="what a play", mid="chat-1") -> bytes:
    """A chat.message.sent body in the shape Kick's event docs give."""
    return json.dumps({
        "message_id": mid, "replies_to": None,
        "broadcaster": {"is_anonymous": False, "user_id": bid, "username": "LaCobraaa",
                        "is_verified": True, "channel_slug": "lacobraaa"},
        "sender": {"is_anonymous": False, "user_id": 77, "username": user,
                   "is_verified": False, "channel_slug": user,
                   "identity": {"username_color": "#FF5733", "badges": []}},
        "content": text, "emotes": [], "created_at": "2026-10-14T12:00:00Z"}).encode()


def _signed(body: bytes, priv=KICK_KEY, delivery="01JDELIVERY0001",
            ts="2026-10-14T12:00:00Z", etype="chat.message.sent") -> dict:
    sig = priv.sign(f"{delivery}.{ts}.".encode() + body, padding.PKCS1v15(), hashes.SHA256())
    return {"Kick-Event-Message-Id": delivery, "Kick-Event-Subscription-Id": "sub_1",
            "Kick-Event-Signature": base64.b64encode(sig).decode(),
            "Kick-Event-Message-Timestamp": ts, "Kick-Event-Type": etype,
            "Kick-Event-Version": "1"}


class FakeKick:
    """Kick's public API as the module sees it: the key endpoint and the
    subscriptions endpoint, every call recorded."""

    def __init__(self):
        self.key_status, self.key_body = 200, {"data": {"public_key": _pem(KICK_KEY)}, "message": "OK"}
        self.create = []            # scripted (status, body) answers; default success
        self.delete_status = 204
        self.calls = []
        self.made = 0
        self.tokens = 0

    async def http(self, method, url, *, headers=None, params=None, json_body=None):
        self.calls.append((method, url, headers, params, json_body))
        if url == ke.PUBLIC_KEY_URL:
            return self.key_status, self.key_body
        if url == ke.SUBSCRIPTIONS_URL and method == "POST":
            if self.create:
                return self.create.pop(0)
            self.made += 1
            return 200, {"data": [{"name": "chat.message.sent", "version": 1,
                                   "subscription_id": f"sub_{self.made}"}], "message": "OK"}
        if url == ke.SUBSCRIPTIONS_URL and method == "DELETE":
            return self.delete_status, None
        raise AssertionError(f"unexpected {method} {url}")

    async def token(self):
        self.tokens += 1
        return "APP"

    def of(self, method, url=None):
        return [c for c in self.calls if c[0] == method and (url is None or c[1] == url)]


@pytest.fixture(autouse=True)
def kick(tmp_path, monkeypatch):
    fake = FakeKick()
    monkeypatch.setattr(ke, "_http", fake.http)
    monkeypatch.setattr(ke, "_app_token", fake.token)
    monkeypatch.setattr(ke, "_SUBS_FILE", tmp_path / "kick_event_subscriptions.json")
    for name, value in (("_subs", {}), ("_subs_loaded", False), ("_inflight", set()),
                        ("_attempted", {}), ("_listeners", {}), ("_seen", {}),
                        ("_key", None), ("_key_tried", 0.0), ("_reject_logged", 0.0),
                        ("_stats", {k: 0 for k in ke._stats})):
        monkeypatch.setattr(ke, name, value)
    return fake


def _deliver(body: bytes, headers: dict) -> int:
    return asyncio.run(ke.handle_delivery(headers, body))


def _drain(q) -> list:
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


# ── 1. a signed delivery reaches the channel's listener ─────────────────────

def test_a_signed_message_reaches_the_session_listening_for_that_channel():
    mine, other = ke.listen("1001"), ke.listen("2002")
    body = _chat(text="LETS GOOO")
    assert _deliver(body, _signed(body)) == 200
    assert _drain(mine) == [("viewer1", "LETS GOOO")]
    assert _drain(other) == [], "another channel's session heard this chat"


def test_every_session_on_the_channel_hears_every_message():
    """Two users monitoring the same Kick channel: one subscription, and both
    engines see the chat."""
    a, b = ke.listen("1001"), ke.listen("1001")
    body = _chat()
    _deliver(body, _signed(body))
    assert _drain(a) == _drain(b) == [("viewer1", "what a play")]


def test_the_real_endpoint_takes_kicks_post_with_no_session(monkeypatch):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    assert "/kick/webhook" in api._OPEN_PATHS
    q = ke.listen("1001")
    c = TestClient(api.app, base_url="https://testserver")
    body = _chat()
    r = c.post("/kick/webhook", content=body,
               headers={**_signed(body), "content-type": "application/json"},
               follow_redirects=False)
    assert r.status_code == 200, r.text
    assert _drain(q) == [("viewer1", "what a play")]
    # The signature is over the RAW body: the same JSON re-serialised (other
    # spacing) under the original signature is refused, as it must be.
    reserialised = json.dumps(json.loads(body), separators=(",", ":")).encode()
    assert reserialised != body
    r = c.post("/kick/webhook", content=reserialised,
               headers={**_signed(body, delivery="01JDELIVERY0002"),
                        "content-type": "application/json"})
    assert r.status_code == 401 and _drain(q) == []
    assert c.post("/kick/webhook", content=body).status_code == 400
    big = b"x" * (ke.MAX_BODY + 1)
    assert c.post("/kick/webhook", content=big, headers=_signed(big)).status_code == 413
    assert c.get("/kick/webhook", follow_redirects=False).status_code != 200


# ── 2. nothing unsigned reaches anyone ───────────────────────────────────────

def test_a_tampered_body_or_a_strangers_key_is_refused():
    q = ke.listen("1001")
    body = _chat(text="calm")
    forged = body.replace(b"calm", b"CLIP IT")
    assert _deliver(forged, _signed(body)) == 401
    other = _chat(text="CLIP IT", mid="chat-9")
    assert _deliver(other, _signed(other, priv=FORGER_KEY)) == 401
    headers = _signed(body)
    headers.pop("Kick-Event-Signature")
    assert _deliver(body, headers) == 400
    assert _drain(q) == []


def test_a_rotated_key_is_fetched_again_once(kick):
    """The cached key is Kick's old one; the endpoint now serves the new one."""
    ke._key = serialization.load_pem_public_key(_pem(FORGER_KEY).encode())
    q = ke.listen("1001")
    body = _chat()
    assert _deliver(body, _signed(body)) == 200
    assert _drain(q) == [("viewer1", "what a play")]
    assert len(kick.of("GET", ke.PUBLIC_KEY_URL)) == 1


def test_a_stream_of_forgeries_does_not_hammer_kick_for_the_key(kick):
    _deliver(_chat(), _signed(_chat()))                   # key loaded
    ke._key_tried -= ke._KEY_REFRESH_S + 1                # a re-fetch is due
    for i in range(50):
        body = _chat(mid=f"f{i}")
        assert _deliver(body, _signed(body, priv=FORGER_KEY, delivery=f"D{i}")) == 401
    assert len(kick.of("GET", ke.PUBLIC_KEY_URL)) == 2, "re-fetch is not rate-limited"


def test_no_key_is_503_and_kick_is_not_asked_on_every_delivery(kick):
    kick.key_status, kick.key_body = 500, {"message": "down"}
    for i in range(10):
        body = _chat(mid=f"m{i}")
        assert _deliver(body, _signed(body, delivery=f"D{i}")) == 503
    assert len(kick.of("GET", ke.PUBLIC_KEY_URL)) == 1


@pytest.mark.parametrize("shape", ["envelope", "plain", "one_line", "escaped", "pkcs1", "bare"])
def test_the_key_is_read_whatever_shape_the_endpoint_answers_in(shape):
    """Kick's docs do not give the key endpoint's response schema."""
    pem = _pem(KICK_KEY)
    inner = "".join(pem.splitlines()[1:-1])
    body = {"envelope": {"data": {"public_key": pem}, "message": "OK"},
            "plain": pem,
            "one_line": {"data": {"key": f"-----BEGIN PUBLIC KEY----- {inner} -----END PUBLIC KEY-----"}},
            "escaped": {"public_key": pem.replace("\n", "\\n")},
            "pkcs1": {"data": {"public_key": _pem(KICK_KEY, serialization.PublicFormat.PKCS1)}},
            "bare": {"data": {"public_key": inner}}}[shape]
    key = ke._parse_key(body)
    assert key is not None
    msg = _chat()
    h = _signed(msg)
    assert ke._signed_by(key, h["Kick-Event-Message-Id"], h["Kick-Event-Message-Timestamp"],
                         msg, h["Kick-Event-Signature"])


# ── 3. signed means 200 ──────────────────────────────────────────────────────

def test_signed_deliveries_nobody_uses_are_still_answered_200():
    body = _chat(bid=999)                                  # no session listening
    assert _deliver(body, _signed(body)) == 200
    status = json.dumps({"broadcaster": {"user_id": 1001}, "is_live": False}).encode()
    assert _deliver(status, _signed(status, etype="livestream.status.updated")) == 200
    junk = b"not json"
    assert _deliver(junk, _signed(junk, delivery="D-junk")) == 200


# ── 4. once per message ──────────────────────────────────────────────────────

def test_the_same_chat_message_delivered_twice_counts_once():
    """Kick retries, and a renewed subscription overlaps its predecessor:
    different delivery ids, the same chat message id."""
    q = ke.listen("1001")
    body = _chat(mid="chat-42")
    _deliver(body, _signed(body, delivery="01JDELIVERY-A"))
    _deliver(body, _signed(body, delivery="01JDELIVERY-B"))
    assert len(_drain(q)) == 1


# ── 5. emotes read like Twitch's ─────────────────────────────────────────────

def test_an_emote_tag_reads_as_its_name_so_hype_weighting_applies():
    from src.chat.metrics import emote_weight
    q = ke.listen("1001")
    body = _chat(text="[emote:37226:KEKW]")
    _deliver(body, _signed(body))
    (author, text), = _drain(q)
    assert text == "KEKW" and emote_weight(text) == 2.5
    assert emote_weight("[emote:37226:KEKW]") == 1.0, "the tag itself was never weighted"
    assert ke.plain_text("lol [emote:1:Pog] nice [emote:22:KEKW]") == "lol Pog nice KEKW"


# ── 6. subscribing ───────────────────────────────────────────────────────────

def test_subscribing_sends_the_documented_request_and_records_it(kick):
    assert asyncio.run(ke.ensure_subscribed("1001", "LaCobraaa")) is True
    (method, url, headers, params, body), = kick.of("POST", ke.SUBSCRIPTIONS_URL)
    assert body == {"broadcaster_user_id": 1001,
                    "events": [{"name": "chat.message.sent", "version": 1}],
                    "method": "webhook"}
    assert headers["Authorization"] == "Bearer APP"
    saved = json.loads(ke._SUBS_FILE.read_text())
    assert saved["1001"]["id"] == "sub_1" and saved["1001"]["slug"] == "lacobraaa"
    # The key is warmed with the subscription, so the first message verifies.
    assert ke._key is not None


def test_a_recorded_subscription_is_not_made_again_even_after_a_restart(kick):
    asyncio.run(ke.ensure_subscribed("1001", "lacobraaa"))
    ke._subs.clear()                                       # a fresh process:
    ke._subs_loaded = False                                # nothing in memory,
    ke._attempted.clear()                                  # only the file
    assert asyncio.run(ke.ensure_subscribed("1001", "lacobraaa")) is True
    assert len(kick.of("POST", ke.SUBSCRIPTIONS_URL)) == 1


def test_a_renewal_is_rate_limited_and_creates_before_it_deletes(kick):
    asyncio.run(ke.ensure_subscribed("1001", "lacobraaa"))
    asyncio.run(ke.ensure_subscribed("1001", "lacobraaa", renew=True))
    assert len(kick.of("POST", ke.SUBSCRIPTIONS_URL)) == 1, "renewed inside RENEW_GAP_S"
    ke._attempted["1001"] -= ke.RENEW_GAP_S + 1
    assert asyncio.run(ke.ensure_subscribed("1001", "lacobraaa", renew=True)) is True
    order = [(c[0], (c[4] or {}).get("broadcaster_user_id") or c[3]) for c in kick.calls
             if c[1] == ke.SUBSCRIPTIONS_URL]
    assert order == [("POST", 1001), ("POST", 1001), ("DELETE", [("id", "sub_1")])]
    assert ke._subs["1001"]["id"] == "sub_2"


@pytest.mark.parametrize("dup", [
    (409, {"message": "subscription already exists"}),
    (200, {"data": [{"name": "chat.message.sent", "version": 1, "error": "already subscribed"}]}),
])
def test_a_renewal_kick_refuses_as_a_duplicate_replaces_the_old_one(kick, dup):
    """Kick's docs do not say whether a second subscription for the same
    channel is allowed; if it is refused, the quiet one is replaced."""
    asyncio.run(ke.ensure_subscribed("1001", "lacobraaa"))
    ke._attempted["1001"] -= ke.RENEW_GAP_S + 1
    kick.create = [dup]
    assert asyncio.run(ke.ensure_subscribed("1001", "lacobraaa", renew=True)) is True
    order = [(c[0], c[3]) for c in kick.calls if c[1] == ke.SUBSCRIPTIONS_URL]
    assert order == [("POST", None), ("POST", None), ("DELETE", [("id", "sub_1")]), ("POST", None)]
    assert ke._subs["1001"]["id"] == "sub_2"


def test_a_renewal_refused_for_auth_or_rate_never_deletes_the_working_one(kick):
    asyncio.run(ke.ensure_subscribed("1001", "lacobraaa"))
    ke._attempted["1001"] -= ke.RENEW_GAP_S + 1
    kick.create = [(429, {"message": "slow down"})]
    assert asyncio.run(ke.ensure_subscribed("1001", "lacobraaa", renew=True)) is True
    assert kick.of("DELETE") == [] and ke._subs["1001"]["id"] == "sub_1"


@pytest.mark.parametrize("answer", [
    (403, {"message": "Forbidden"}),
    (200, {"data": [{"name": "chat.message.sent", "version": 1,
                     "error": "webhook url is not configured"}]}),
])
def test_a_refusal_is_logged_not_recorded_and_retried_later(kick, answer):
    kick.create = [answer]
    assert asyncio.run(ke.ensure_subscribed("1001", "lacobraaa")) is False
    assert "1001" not in ke._subs and not ke._SUBS_FILE.exists()
    assert asyncio.run(ke.ensure_subscribed("1001", "lacobraaa")) is False
    assert len(kick.of("POST", ke.SUBSCRIPTIONS_URL)) == 1, "retried inside RETRY_GAP_S"
    ke._attempted["1001"] -= ke.RETRY_GAP_S + 1
    assert asyncio.run(ke.ensure_subscribed("1001", "lacobraaa")) is True


def test_an_expired_token_is_replaced_once(kick, monkeypatch):
    forgot = []
    monkeypatch.setattr(ke, "_forget_token", lambda: forgot.append(1))
    kick.create = [(401, {"message": "Unauthorized"})]
    assert asyncio.run(ke.ensure_subscribed("1001", "lacobraaa")) is True
    assert forgot == [1] and kick.tokens == 2


def test_without_kick_app_credentials_nothing_is_sent(kick, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(ke, "_app_token", REAL_APP_TOKEN)
    monkeypatch.setattr(settings, "kick_client_id", "")
    monkeypatch.setattr(settings, "kick_client_secret", "")
    assert asyncio.run(ke.ensure_subscribed("1001", "lacobraaa")) is False
    assert kick.of("POST") == []


# ── 7. pruning ───────────────────────────────────────────────────────────────

def test_a_channel_nobody_lists_is_unsubscribed_and_a_listed_one_is_kept(kick):
    asyncio.run(ke.ensure_subscribed("1001", "lacobraaa"))
    asyncio.run(ke.ensure_subscribed("2002", "westcol"))
    asyncio.run(ke.ensure_subscribed("3003", "gone"))
    ke.listen("3003")                     # a session is still live on it
    assert asyncio.run(ke.prune({"lacobraaa"})) == 1
    assert kick.of("DELETE")[0][3] == [("id", "sub_2")]
    assert set(ke._subs) == {"1001", "3003"}
    assert set(json.loads(ke._SUBS_FILE.read_text())) == {"1001", "3003"}


def test_a_failed_unsubscribe_is_kept_for_the_next_pass(kick):
    asyncio.run(ke.ensure_subscribed("2002", "westcol"))
    kick.delete_status = 500
    assert asyncio.run(ke.prune(set())) == 0 and "2002" in ke._subs
    kick.delete_status = 404                                # already gone at Kick
    assert asyncio.run(ke.prune(set())) == 1 and "2002" not in ke._subs


def test_the_prune_task_survives_its_own_errors(monkeypatch):
    """It runs in the same gather as the dashboard: one exception escaping
    would stop the whole server."""
    monkeypatch.setattr(ke, "PRUNE_FIRST_S", 0)
    monkeypatch.setattr(ke, "PRUNE_EVERY_S", 0.01)
    calls = []

    def monitored():
        calls.append(1)
        raise RuntimeError("streams unreadable")

    async def go():
        t = asyncio.create_task(ke.prune_task(monitored))
        await asyncio.sleep(0.1)
        assert not t.done(), "the prune task died"
        t.cancel()
    asyncio.run(go())
    assert len(calls) > 2


def test_the_server_starts_the_prune_task_with_the_kick_channels_listed():
    import inspect
    from src import main
    src = inspect.getsource(main.main)
    assert "_kick_events.prune_task(" in src and 'name="kick-events-prune"' in src
    assert 's.get("platform") == "kick"' in src


# ── 8. the monitor ───────────────────────────────────────────────────────────

def test_chat_reaches_the_engine_through_the_monitor(kick):
    got = []

    async def on_msg(author, text):
        got.append((author, text))

    async def go():
        m = KickChatMonitor("1001", on_msg, slug="LaCobraaa")
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0.05)
        assert ke._listeners.get("1001"), "the session is not listening"
        body = _chat(text="[emote:1:POGGERS]")
        assert await ke.handle_delivery(_signed(body), body) == 200
        await asyncio.sleep(0.05)
        m.stop()
        await asyncio.wait_for(task, 1)
        assert "1001" not in ke._listeners, "a finished session is still listening"
    asyncio.run(go())
    assert got == [("viewer1", "POGGERS")]
    assert ke._subs["1001"]["slug"] == "lacobraaa"


def test_a_quiet_live_chat_gets_its_subscription_renewed(kick, monkeypatch):
    monkeypatch.setattr(KickChatMonitor, "TICK_S", 0.01)
    monkeypatch.setattr(KickChatMonitor, "SILENCE_S", 0.03)
    monkeypatch.setattr(ke, "RENEW_GAP_S", 0.0)

    async def on_msg(author, text):
        pass

    async def go():
        m = KickChatMonitor("1001", on_msg, slug="lacobraaa")
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0.15)
        m.stop()
        await asyncio.wait_for(task, 1)
    asyncio.run(go())
    assert len(kick.of("POST", ke.SUBSCRIPTIONS_URL)) >= 2
    assert kick.of("DELETE")[0][3] == [("id", "sub_1")]


def test_a_channel_without_a_broadcaster_id_waits_instead_of_ending_the_session(kick):
    """The worker ends a session the moment ANY of its tasks finishes, so a
    chat monitor that returned would restart the whole session (meter,
    recorder, cuts) every half-minute. It must wait, quietly, until stopped."""
    async def on_msg(author, text):
        raise AssertionError("no chat expected")

    async def go():
        m = KickChatMonitor("", on_msg, slug="xqc")
        task = asyncio.create_task(m.run())
        await asyncio.sleep(0.1)
        assert not task.done(), "the monitor returned and would end the session"
        assert m._running
        m.stop()
        await asyncio.wait_for(task, 1)
        assert m._running is False
        m2 = KickChatMonitor("", on_msg)
        t2 = asyncio.create_task(m2.run())
        await asyncio.sleep(0.02)
        t2.cancel()                                       # how the worker ends it
        with pytest.raises(asyncio.CancelledError):
            await t2
    asyncio.run(go())
    assert kick.calls == []


def test_the_worker_hands_the_monitor_the_broadcaster_id():
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker.StreamWorker._run_chat)
    assert "KickChatMonitor(info.broadcaster_id, on_message, slug=info.channel)" in src


# ── 9. the broadcaster id ────────────────────────────────────────────────────

def test_the_platform_gives_the_broadcaster_id_from_the_public_api_or_the_site(tmp_path, monkeypatch):
    from src.ingestion.platform import kick as kick_mod
    from tests.test_kick_live import (PUBLIC_LIVE, SITE, TOKEN, Scripted, _configured, _run)
    monkeypatch.setattr(kick_mod, "_CHATROOMS_FILE", tmp_path / "kick_chatrooms.json")
    monkeypatch.setattr(kick_mod, "_chatrooms", {})
    monkeypatch.setattr(kick_mod, "_chatrooms_loaded", False)
    _configured(monkeypatch)
    p = Scripted({"id.kick.com/oauth/token": TOKEN, "api.kick.com/public/v1/channels": PUBLIC_LIVE,
                  "kick.com/api/v2/channels/xqc": SITE})
    assert _run(p.get_stream_info("xqc")).broadcaster_id == "1"
    # The site endpoint blocked: chat no longer depends on it at all.
    p = Scripted({"id.kick.com/oauth/token": TOKEN, "api.kick.com/public/v1/channels": PUBLIC_LIVE,
                  "kick.com/api/v2/channels/xqc": (403, None)})
    assert _run(p.get_stream_info("xqc")).broadcaster_id == "1"
    # No app credentials: the site's user id stands in.
    _configured(monkeypatch, on=False)
    site = (200, {**SITE[1], "user_id": 676})
    p = Scripted({"kick.com/api/v2/channels/xqc": site})
    assert _run(p.get_stream_info("xqc")).broadcaster_id == "676"
