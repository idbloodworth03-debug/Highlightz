"""THE SPLIT, stage 3: a channel's profile is held by the worker watching it.

docs/HANDOFF.md, "THE SPLIT". The stream worker scores against the profile
every second and saves it every 30. The dashboard changes it on approve,
reject, undo, sensitivity and preset. In one process they share the object.
Split, a change made in the web process's own copy would never reach the
detector and would be overwritten by the worker's next save. So:

  * a change is a small dict made by ONE function (src/profiles/ops.apply)
    in whichever process holds the profile;
  * the dashboard asks the gateway `owns(uid, channel)` and sends the change
    there, or makes it itself exactly as it always did;
  * with the switch off there is no gateway and nothing is different.

The last section starts REAL worker processes.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import signal
import time

import pytest
from itsdangerous import TimestampSigner

from src.dashboard import api, ingest as ingest_mod, undo
from src.ingestion import pool as pool_mod
from src.profiles import manager, ops
from src.profiles.profile import StreamerProfile

REPO = os.getcwd()


# ── the one function that makes a change ─────────────────────────────────────

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(manager.settings, "local_storage_path", str(tmp_path))
    monkeypatch.setattr(manager, "_user_profile_managers", {})
    return tmp_path


def _disk(store, uid, channel) -> dict:
    return json.loads((store / "profiles" / uid / f"{channel}.json").read_text())


def test_a_reject_then_its_undo_lands_exactly_where_it_started(store):
    async def go():
        first = await ops.apply("u1", "nova", {"op": "set"})           # nothing to change
        first["saved"] = (store / "profiles" / "u1" / "nova.json").exists()
        rej = await ops.apply("u1", "nova", {"op": "record_clip", "approved": False,
                                             "signals": [{"type": "CHAT_VELOCITY", "value": 0.9}]})
        back = await ops.apply("u1", "nova", {"op": "restore", "snap": rej["before"]})
        return first, rej, back
    first, rej, back = asyncio.run(go())
    assert first["touched"] is False and first["saved"] is False       # and nothing was written for it
    assert rej["touched"] and rej["profile"]["rejected_clips"] == 1
    assert rej["profile"]["trigger_threshold"] > rej["before"]["trigger_threshold"]
    assert rej["before"] == ops.snapshot(StreamerProfile(channel="nova"))
    for k in ("trigger_threshold", "signal_weights", "total_clips", "approved_clips", "rejected_clips"):
        assert back["profile"][k] == rej["before"][k], k
    assert _disk(store, "u1", "nova")["rejected_clips"] == 0


def test_set_changes_only_what_differs_and_saves_only_then(store):
    async def go():
        a = await ops.apply("u1", "nova", {"op": "set", "sensitivity": 2, "preset": None})
        b = await ops.apply("u1", "nova", {"op": "set", "sensitivity": 2, "preset": None})
        c = await ops.apply("u1", "nova", {"op": "set", "sensitivity": None, "preset": "fps"})
        return a, b, c
    a, b, c = asyncio.run(go())
    assert a["touched"] and a["profile"]["sensitivity"] == 2
    assert b["touched"] is False
    assert c["touched"] and c["profile"]["preset"] == "fps" and c["profile"]["sensitivity"] == 2
    with pytest.raises(ValueError):
        asyncio.run(ops.apply("u1", "nova", {"op": "delete-everything"}))


def test_undo_still_has_its_two_functions_and_they_are_these():
    assert undo.snapshot_profile is ops.snapshot and undo.restore_profile is ops.restore


# ── the dashboard sends the change where the profile is ──────────────────────

class _Gateway:
    """Stands in for a worker: holds the profile, applies what it is sent."""
    def __init__(self, held=("nova",)):
        self.held, self.calls = set(held), []
        self.profiles: dict[str, StreamerProfile] = {}

    def owns(self, uid, channel):
        return channel in self.held

    async def change(self, uid, channel, op, platform="twitch"):
        self.calls.append((uid, channel, op, platform))
        p = self.profiles.setdefault(channel, StreamerProfile(channel=channel))
        before = ops.snapshot(p)
        touched = True
        if op["op"] == "record_clip":
            p.record_clip(approved=op["approved"], signals=op["signals"])
        elif op["op"] == "restore":
            ops.restore(p, op["snap"])
        else:
            touched = False
            if op.get("sensitivity") is not None and op["sensitivity"] != p.sensitivity:
                p.sensitivity, touched = op["sensitivity"], True
            if op.get("preset") is not None:
                p.preset, touched = op["preset"], True
        return {"profile": p.to_dict(), "before": before, "touched": touched}


@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.auth import users as user_store
    from src.stats import stream_stats
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(stream_stats, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_streams", {})
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_save_streams", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)
    monkeypatch.setattr(undo, "_stacks", {})
    sent = []

    async def bcast(event, user_id=None):
        sent.append(event)
    monkeypatch.setattr(api, "broadcast", bcast)

    class _NoLocal:
        """Any use of the web process's own profile is a failure here."""
        async def load(self, *a, **k):
            raise AssertionError("the dashboard changed its own copy of a profile a worker holds")
        save = get = load
    monkeypatch.setattr(manager, "get_profile_manager", lambda uid: _NoLocal())

    now = time.time()
    (tmp_path / "users.json").write_text(json.dumps([
        {"id": "u1", "username": "one", "subscription_status": "active", "plan": "pro", "created_at": now}]))
    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(json.dumps(
        {"auth": True, "user_id": "u1", "username": "u1", "is_admin": False,
         "subscription_status": "active"}).encode())).decode())

    def add(cid, channel="nova", **extra):
        api._clips[cid] = {"id": cid, "user_id": "u1", "status": "pending", "channel": channel,
                           "created_at": now - 60, "trigger_score": 80.0,
                           "trigger_signals": [{"type": "CHAT_VELOCITY", "value": 0.9}], **extra}
    gw = _Gateway()
    monkeypatch.setattr(api, "_profile_gateway", gw)
    c.add, c.gw, c.sent = add, gw, sent
    return c


def test_reject_and_undo_are_made_in_the_worker_and_undo_restores_its_snapshot(client):
    client.add("c1")
    assert client.post("/clips/c1/reject").status_code == 200
    uid, channel, op, _ = client.gw.calls[0]
    assert (uid, channel, op["op"], op["approved"]) == ("u1", "nova", "record_clip", False)
    assert op["signals"] == [{"type": "CHAT_VELOCITY", "value": 0.9}]
    held = client.gw.profiles["nova"]
    fresh = StreamerProfile(channel="nova")
    assert held.trigger_threshold > fresh.trigger_threshold and held.rejected_clips == 1
    told = [e for e in client.sent if e["event"] == "profile_updated"]
    assert told and told[-1]["profile"]["rejected_clips"] == 1
    assert client.post("/clips/undo").json()["restored"] == 1
    assert client.gw.calls[1][2]["op"] == "restore"
    assert client.gw.calls[1][2]["snap"] == ops.snapshot(fresh)
    assert held.trigger_threshold == fresh.trigger_threshold and held.rejected_clips == 0
    assert [e for e in client.sent if e["event"] == "profile_updated"][-1]["profile"]["rejected_clips"] == 0


def test_approve_is_taught_in_the_worker(client):
    client.add("c1")
    assert client.post("/clips/c1/approve").status_code == 200
    assert [(c[1], c[2]["op"], c[2]["approved"]) for c in client.gw.calls] == [("nova", "record_clip", True)]
    assert client.gw.profiles["nova"].approved_clips == 1
    assert any(e["event"] == "profile_updated" and e["profile"]["approved_clips"] == 1 for e in client.sent)


def test_a_clip_the_formula_did_not_produce_teaches_nobody(client):
    """A Highlight (crowd suggestion) is excluded from learning, split or not."""
    client.add("s1", suggested=True)
    client.add("s2", suggested=True)
    client.post("/clips/s1/approve")
    client.post("/clips/s2/reject")
    assert client.gw.calls == []
    assert not [e for e in client.sent if e["event"] == "profile_updated"]


def test_a_channel_no_worker_holds_is_changed_here_as_it_always_was(monkeypatch, client):
    """`owns` false: the original code path, on the web process's own copy."""
    local = StreamerProfile(channel="elsewhere")
    saved = []

    class _PM:
        async def load(self, ch, *a):
            return local

        async def save(self, p):
            saved.append(p.rejected_clips)
    monkeypatch.setattr(manager, "get_profile_manager", lambda uid: _PM())
    client.add("c9", channel="elsewhere")
    client.post("/clips/c9/reject")
    assert client.gw.calls == [] and local.rejected_clips == 1 and saved == [1]
    client.post("/clips/undo")
    assert local.rejected_clips == 0 and client.gw.calls == []


def test_with_the_switch_off_there_is_no_gateway_at_all():
    import inspect
    assert api._profile_held_elsewhere("u1", "nova") is False or api._profile_gateway is not None
    src = inspect.getsource(api)
    assert src.count("_profile_held_elsewhere(") == 5            # the helper and four call sites
    assert "_profile_gateway = None" in src
    assert "dashboard_api.set_profile_gateway(ingest)" in open("src/main.py").read()


# ── end to end: real worker processes ────────────────────────────────────────

FAKE_WORKER = '''
import asyncio, sys
sys.path.insert(0, %(repo)r)
from src.ingestion import host, worker_main, workers
from src.profiles.manager import get_profile_manager


class Scripted:
    """Holds the channel's profile the way a real stream worker does."""
    def __init__(self, config, platform, queue, shared_buffers, on_score):
        self.c, self._stop, self.profile = config, asyncio.Event(), None

    async def start(self):
        c = self.c
        pm = get_profile_manager(c.user_id)
        try:
            self.profile = await pm.load(c.channel, c.platform_name)
            host.set_status(c.user_id + ":" + c.channel, "live")
            await host.broadcast({"event": "stream_status", "channel": c.channel, "status": "live"},
                                 user_id=c.user_id)
            await self._stop.wait()
        except asyncio.CancelledError:
            return
        finally:
            if self.profile is not None:
                self.profile.total_sessions += 1            # something only the worker does
                await pm.save(self.profile)                 # as StreamWorker._cleanup does

    def stop(self):
        self._stop.set()


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
def world(tmp_path, monkeypatch):
    data = tmp_path / "store"
    data.mkdir()
    (tmp_path / "fakeingest.py").write_text(FAKE_WORKER % {"repo": REPO})
    monkeypatch.setenv("LOCAL_STORAGE_PATH", str(data))              # the worker processes' store
    monkeypatch.setattr(manager.settings, "local_storage_path", str(data))   # and this one's: the same
    monkeypatch.setattr(manager, "_user_profile_managers", {})
    monkeypatch.setattr(pool_mod, "_ROOT", tmp_path)
    monkeypatch.setattr(pool_mod, "BACKOFF_S", (0.05,) * 5)
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: {"id": uid} if uid else None)
    monkeypatch.setattr("src.billing.plans.uid_priority_level", lambda uid: 0)
    monkeypatch.setattr("src.billing.plans.uid_has_priority", lambda uid: False)
    sent = []

    async def bcast(event, user_id=None):
        sent.append(event)
    monkeypatch.setattr(api, "broadcast", bcast)
    monkeypatch.setattr(api, "_live_slots", set())
    monkeypatch.setattr(api, "_streams", {"u1:nova": {"channel": "nova", "platform": "kick", "preset": "default",
                                                      "status": "offline", "user_id": "u1"}})
    return data


REJECT = {"op": "record_clip", "approved": False, "signals": [{"type": "CHAT_VELOCITY", "value": 0.9}]}


def test_a_change_is_made_in_the_worker_that_holds_the_profile(world):
    async def go():
        ing = ingest_mod.Ingest(1, module="fakeingest")
        await ing.start()
        try:
            assert await _until(lambda: api._streams["u1:nova"]["status"] == "live")
            assert ing.owns("u1", "nova") and not ing.owns("u1", "other") and not ing.owns("u2", "nova")
            rej = await ing.change("u1", "nova", REJECT, "kick")
            held = await ing.change("u1", "nova", {"op": "set"})          # what the worker has in memory
            web_copy = await manager.get_profile_manager("u1").get("nova")
            back = await ing.change("u1", "nova", {"op": "restore", "snap": rej["before"]})
            return rej, held, web_copy.to_dict() if web_copy else None, back
        finally:
            await ing.stop()
    rej, held, web_copy, back = asyncio.run(go())
    assert rej["profile"]["rejected_clips"] == 1
    assert rej["profile"]["trigger_threshold"] > rej["before"]["trigger_threshold"]
    assert held["profile"] == rej["profile"], "the worker's own object did not change"
    assert web_copy == rej["profile"], "the web process's copy fell behind"
    # What goes on to the tabs is whole: keeping a copy must not eat keys from it.
    assert "approval_rate" in rej["profile"] and "velocity_spike_multiplier" in rej["profile"]
    assert back["profile"]["rejected_clips"] == 0
    assert back["profile"]["trigger_threshold"] == rej["before"]["trigger_threshold"]


def test_when_the_worker_lets_go_the_web_process_takes_over_and_hands_back(world):
    async def go():
        ing = ingest_mod.Ingest(1, module="fakeingest")
        await ing.start()
        try:
            assert await _until(lambda: api._streams["u1:nova"]["status"] == "live")
            await ing.change("u1", "nova", REJECT, "kick")
            # Paused: the record first, then the stop (as _pause_user_streams does).
            api._streams["u1:nova"]["status"] = "paused"
            await ing.publish_remove("nova", "u1")
            assert await _until(lambda: not ing.owns("u1", "nova"))
            final = (await manager.get_profile_manager("u1").get("nova")).to_dict()
            on_disk = json.loads((world / "profiles" / "u1" / "nova.json").read_text())
            # Nobody holds it now: a change is made here, on what the worker left.
            local = await ing.change("u1", "nova", REJECT, "kick")
            # They come back: the worker reads the file again, with that change in it.
            api._streams["u1:nova"]["status"] = "starting"
            await ing.publish_new("nova", "kick", "default", "u1")
            assert await _until(lambda: api._streams["u1:nova"]["status"] == "live" and ing.owns("u1", "nova"))
            again = await ing.change("u1", "nova", {"op": "set"})
            return final, on_disk, local, again
        finally:
            await ing.stop()
    final, on_disk, local, again = asyncio.run(go())
    assert final["rejected_clips"] == 1 and final["total_sessions"] == on_disk["total_sessions"] >= 1, \
        "the web process did not get the worker's last state"
    assert final == on_disk
    assert local["profile"]["rejected_clips"] == 2
    assert again["profile"]["rejected_clips"] == 2, "the returning worker did not see the dashboard's change"


def test_a_dead_worker_does_not_take_the_change_with_it(world):
    async def go():
        ing = ingest_mod.Ingest(1, module="fakeingest")
        await ing.start()
        try:
            assert await _until(lambda: api._streams["u1:nova"]["status"] == "live")
            await ing.change("u1", "nova", REJECT, "kick")               # saved by the worker
            pid = ing.pool.workers[0].proc.pid
            os.kill(pid, signal.SIGKILL)
            res = await ing.change("u1", "nova", REJECT, "kick")         # asked while it is dying or dead
            assert await _until(lambda: ing.owns("u1", "nova") and ing.pool.workers[0].proc.pid != pid)
            again = await ing.change("u1", "nova", {"op": "set"})
            return res, again
        finally:
            await ing.stop()
    res, again = asyncio.run(go())
    assert res["profile"]["rejected_clips"] == 2, "the change was dropped with the worker"
    assert again["profile"]["rejected_clips"] == 2
