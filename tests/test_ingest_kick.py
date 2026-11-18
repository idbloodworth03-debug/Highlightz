"""THE SPLIT, stage 3: Kick chat reaches a worker in another process.

docs/HANDOFF.md, "THE SPLIT". Kick delivers chat as webhooks, to the web
process. A Kick channel watched in a worker process has to be SENT what was
delivered, and must not talk to Kick about subscriptions itself: that state
has one owner. Held here:

  * in one process (no remote) listen / unlisten / ensure_subscribed are
    exactly what they were;
  * in a worker, the first listener for a broadcaster asks for its chat, the
    last to leave says stop, and ensure_subscribed is asked of the web
    process, with "no answer" meaning "not subscribed";
  * end to end with a real worker process: a message dispatched in the web
    process arrives at the worker's chat session, forwarding stops when the
    channel stops, and nothing is left listening after a worker dies.
"""

from __future__ import annotations

import asyncio
import os
import signal
import time

import pytest

from src.chat.platform import kick_events
from src.dashboard import api, ingest as ingest_mod
from src.ingestion import pool as pool_mod

REPO = os.getcwd()


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(kick_events, "_listeners", {})
    monkeypatch.setattr(kick_events, "_remote", None)


# ── the hooks ────────────────────────────────────────────────────────────────

def test_in_one_process_nothing_is_asked_of_anybody():
    async def go():
        q = kick_events.listen(42)
        assert kick_events._dispatch("42", "ann", "hello") == 1
        got = q.get_nowait()
        kick_events.unlisten(42, q)
        return got, dict(kick_events._listeners)
    assert asyncio.run(go()) == (("ann", "hello"), {})


class _Remote:
    def __init__(self, answer=True):
        self.said, self.answer = [], answer

    def kick_listen(self, bid):
        self.said.append(("listen", bid))

    def kick_unlisten(self, bid):
        self.said.append(("unlisten", bid))

    async def kick_ensure_subscribed(self, bid, slug="", renew=False):
        self.said.append(("ensure", bid, slug, renew))
        return self.answer


def test_in_a_worker_the_first_listener_asks_and_the_last_one_says_stop(monkeypatch):
    r = _Remote()
    monkeypatch.setattr(kick_events, "_remote", r)

    async def go():
        a = kick_events.listen("42")            # two users on one Kick channel, one worker
        b = kick_events.listen(42)
        other = kick_events.listen("7")
        kick_events.unlisten("42", a)
        mid = list(r.said)
        kick_events.unlisten("42", b)
        kick_events.unlisten("7", other)
        kick_events.unlisten("7", other)        # twice is harmless
        return mid
    mid = asyncio.run(go())
    assert mid == [("listen", "42"), ("listen", "7")]
    assert r.said == [("listen", "42"), ("listen", "7"), ("unlisten", "42"), ("unlisten", "7")]


def test_in_a_worker_subscriptions_are_the_web_processes_business(monkeypatch):
    r = _Remote()
    monkeypatch.setattr(kick_events, "_remote", r)

    async def never(*a, **k):
        raise AssertionError("a worker talked to Kick about a subscription")
    monkeypatch.setattr(kick_events, "_create", never)
    monkeypatch.setattr(kick_events, "_authed", never)
    assert asyncio.run(kick_events.ensure_subscribed(42, "nova")) is True
    assert asyncio.run(kick_events.ensure_subscribed("42", "nova", renew=True)) is True
    assert r.said == [("ensure", "42", "nova", False), ("ensure", "42", "nova", True)]


def test_no_answer_about_a_subscription_means_not_subscribed():
    from src.ingestion.bridge import LinkClosed
    from src.ingestion.remote import Remote

    class Dead:
        async def call(self, *a, **k):
            raise LinkClosed("gone")

        def cast(self, *a, **k):
            return False
    r = Remote(Dead())
    assert asyncio.run(r.kick_ensure_subscribed("42", "nova")) is False
    r.kick_listen("42")
    r.kick_unlisten("42")                        # neither raises


# ── end to end: a real worker process ────────────────────────────────────────

FAKE_WORKER = '''
import asyncio, sys
sys.path.insert(0, %(repo)r)
from src.chat.platform import kick_events
from src.ingestion import host, worker_main, workers


class Scripted:
    """A Kick chat session, as src/chat/platform/kick_chat.py runs one."""
    def __init__(self, config, platform, queue, shared_buffers, on_score):
        self.c = config

    async def start(self):
        c = self.c
        q = kick_events.listen("777")
        try:
            ok = await kick_events.ensure_subscribed("777", c.channel)
            host.set_status(c.user_id + ":" + c.channel, "live")
            await host.broadcast({"event": "test_listening", "channel": c.channel, "subscribed": ok},
                                 user_id=c.user_id)
            while True:
                author, content = await q.get()
                await host.broadcast({"event": "test_chat", "channel": c.channel, "author": author,
                                      "content": content}, user_id=c.user_id)
        except asyncio.CancelledError:
            return
        finally:
            kick_events.unlisten("777", q)

    def stop(self):
        pass


workers.StreamWorker = Scripted
sys.exit(worker_main.main())
'''


async def _until(cond, timeout=20.0, every=0.03):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        await asyncio.sleep(every)
    return False


@pytest.fixture
def web(tmp_path, monkeypatch):
    (tmp_path / "fakeingest.py").write_text(FAKE_WORKER % {"repo": REPO})
    monkeypatch.setattr(pool_mod, "_ROOT", tmp_path)
    monkeypatch.setattr(pool_mod, "BACKOFF_S", (30.0,) * 5)          # a dead worker stays dead here
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: {"id": uid} if uid else None)
    monkeypatch.setattr("src.billing.plans.uid_priority_level", lambda uid: 0)
    monkeypatch.setattr("src.billing.plans.uid_has_priority", lambda uid: False)
    sent, asked = [], []

    async def bcast(event, user_id=None):
        sent.append(event)

    async def ensure(bid, slug="", *, renew=False):
        asked.append((bid, slug, renew))
        return True
    monkeypatch.setattr(api, "broadcast", bcast)
    monkeypatch.setattr(api, "_live_slots", set())
    monkeypatch.setattr(api, "_streams", {"u1:nova": {"channel": "nova", "platform": "kick", "preset": "default",
                                                      "status": "offline", "user_id": "u1"}})
    monkeypatch.setattr(kick_events, "ensure_subscribed", ensure)
    return type("Web", (), {"sent": sent, "asked": asked,
                            "chat": staticmethod(lambda: [(e["author"], e["content"]) for e in sent
                                                          if e["event"] == "test_chat"])})


def test_chat_delivered_to_the_web_process_reaches_the_workers_session(web):
    async def go():
        ing = ingest_mod.Ingest(1, module="fakeingest")
        await ing.start()
        try:
            assert await _until(lambda: any(e["event"] == "test_listening" for e in web.sent))
            assert await _until(lambda: "777" in kick_events._listeners)
            # What handle_delivery does once a webhook has verified:
            assert kick_events._dispatch("777", "ann", "LETS GOOO") == 1
            assert kick_events._dispatch("777", "bob", "clip it") == 1
            assert kick_events._dispatch("999", "eve", "wrong channel") == 0
            assert await _until(lambda: len(web.chat()) == 2), web.sent
            # The channel stops: forwarding stops with it.
            api._streams.pop("u1:nova")
            await ing.publish_remove("nova", "u1")
            assert await _until(lambda: "777" not in kick_events._listeners)
            return [e for e in web.sent if e["event"] == "test_listening"][0]
        finally:
            await ing.stop()
    listening = asyncio.run(go())
    assert web.chat() == [("ann", "LETS GOOO"), ("bob", "clip it")]
    assert listening["subscribed"] is True
    assert web.asked == [("777", "nova", False)], "the subscription was not made by the web process"
    assert kick_events._dispatch("777", "late", "nobody home") == 0


def test_nothing_is_left_listening_for_a_worker_that_died(web):
    async def go():
        ing = ingest_mod.Ingest(1, module="fakeingest")
        await ing.start()
        try:
            assert await _until(lambda: "777" in kick_events._listeners)
            os.kill(ing.pool.workers[0].proc.pid, signal.SIGKILL)
            assert await _until(lambda: "777" not in kick_events._listeners)
            return dict(ing._kick_pumps)
        finally:
            await ing.stop()
    assert asyncio.run(go()) == {}
