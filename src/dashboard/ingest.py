"""The web process's side of the split: what it does for its ingest workers.

THE SPLIT, stage 2 (docs/HANDOFF.md, "THE SPLIT"). With INGEST_WORKERS set,
stream workers run in processes of their own (src/ingestion/worker_main.py)
and this is everything the web process does on their behalf:

  * tells the worker that owns a channel to start or stop watching it
    (`publish_new` / `publish_remove`, the pair the dashboard already calls;
    without the split they publish to Redis for this same process);
  * sends a worker its channels every time it comes up, the first time and
    after every restart (`on_ready`), and cleans up after one that is gone
    (`on_lost`);
  * answers what a worker asks through src/ingestion/host.py.

THE WEB PROCESS STAYS THE AUTHORITY, and three rules below are that:
  1. A WORKER SPEAKS ONLY FOR ITS OWN CHANNELS. Every status it reports and
     every slot it asks for is checked against bridge.owner_of. A message
     about a channel that hashes elsewhere is dropped and logged.
  2. "PAUSED" IS THE DASHBOARD'S WORD. A worker's report never replaces it,
     and a paused channel is never given a slot: the stop is already on its
     way to that worker.
  3. THE TAB IS SENT THE REAL RECORD, never the worker's copy of it.
"""

from __future__ import annotations

import structlog

from src.dashboard import api
from src.ingestion.pool import Pool

log = structlog.get_logger(__name__)

_WIRE_FIELDS = ("channel", "platform", "preset", "user_id", "status")


def _key(channel: str, user_id: str | None) -> str:
    return f"{user_id}:{channel}" if user_id else channel


def _channel_of(stream_key: str) -> str:
    return stream_key.split(":", 1)[1] if ":" in stream_key else stream_key


def _wire(rec: dict) -> dict:
    """What a worker needs of a stream record, and nothing else."""
    return {k: rec.get(k) for k in _WIRE_FIELDS}


class Ingest:
    def __init__(self, count: int, *, module: str = "src.ingestion.worker_main",
                 python: str | None = None) -> None:
        self.pool = Pool(count, handlers={
            "broadcast": self.h_broadcast,
            "acquire_live_slot": self.h_acquire,
            "release_live_slot": self.h_release,
            "watchers_of": self.h_watchers,
            "suggestion_room": self.h_room,
            "notify_clip_ready": self.h_clip_ready,
            "on_clip_file_ready": self.h_file_ready,
            "trim_clip_store": self.h_trim,
            "log_error": self.h_log,
            "stopped": self.h_stopped,
            "kick_listen": self.h_kick_listen,
            "kick_unlisten": self.h_kick_unlisten,
            "kick_ensure_subscribed": self.h_kick_ensure,
        }, on_ready=self.on_ready, on_lost=self.on_lost, module=module, python=python)
        # (worker, Kick broadcaster id) -> the task forwarding that
        # broadcaster's chat to that worker.
        self._kick_pumps: dict[tuple[int, str], "asyncio.Task"] = {}
        # stream key -> the worker told to watch it and not yet heard to have
        # stopped. While a key is here that worker holds the channel's
        # PROFILE, and a change to it is sent there (`change`).
        self._watching: dict[str, int] = {}

    async def start(self) -> None:
        await self.pool.start()

    async def stop(self) -> None:
        for task in self._kick_pumps.values():
            task.cancel()
        self._kick_pumps.clear()
        await self.pool.stop()

    def status(self) -> list[dict]:
        return self.pool.status()

    def loop_for(self, stream_key: str) -> dict:
        """The reading the live limit judges for this channel: the loop of
        the worker it hashes to (headroom.allow, `loop`). A worker that is
        down or has not reported reads as too old, which the gate refuses."""
        import time
        index = self.pool.owner(_channel_of(stream_key))
        w = self.pool.workers[index]
        fresh = w.ready and w.beat_at > 0
        return {"name": f"worker {index}", "pct": w.loop_avg, "lag_s": w.lag_avg,
                "age_s": (time.monotonic() - w.beat_at) if fresh else 1e9,
                "live": sum(1 for k in api._live_slots if self._owns(index, _channel_of(k)))}

    def _owns(self, index: int, channel: str) -> bool:
        return bool(channel) and self.pool.owner(channel) == index

    # ── telling workers what to watch ────────────────────────────────────────

    async def publish_new(self, channel: str, platform: str = "twitch",
                          preset: str = "default", user_id: str = "") -> None:
        if user_id:
            from src.auth import users as user_store
            if not user_store.get_by_id(user_id):
                log.warning("stream_msg_unknown_user", user_id=user_id)
                return
        link = self.pool.link_for(channel)
        if link is None:
            # Its worker is down. on_ready sends every unpaused record when it
            # is back, this one included.
            log.warning("ingest_worker_down_for_channel", index=self.pool.owner(channel))
            return
        rec = api._streams.get(_key(channel, user_id)) or {
            "channel": channel, "platform": platform, "preset": preset,
            "user_id": user_id, "status": "starting"}
        if link.cast("spawn", {**_wire(rec), "platform": rec.get("platform") or platform,
                               "preset": rec.get("preset") or preset}):
            self._watching[_key(channel, user_id)] = self.pool.owner(channel)

    async def publish_remove(self, channel: str, user_id: str = "") -> None:
        link = self.pool.link_for(channel)
        if link is not None:
            link.cast("stop", channel, user_id)

    async def on_ready(self, index: int) -> None:
        """Worker `index` is up: at the start, and again after every restart."""
        link = self.pool.workers[index].link
        sent = 0
        for rec in list(api._streams.values()):
            if rec.get("status") == "paused" or not self._owns(index, rec.get("channel", "")):
                continue
            if link.cast("spawn", _wire(rec)):
                self._watching[_key(rec.get("channel", ""), rec.get("user_id") or "")] = index
                sent += 1
        log.info("ingest_worker_given_channels", index=index, channels=sent)

    async def on_lost(self, index: int) -> None:
        """Worker `index` is gone, and everything it ran went with it. Its
        channels hold no slot any more and are not live; say so. on_ready
        starts them again when the next one is up."""
        hit = 0
        for key in [k for k in self._kick_pumps if k[0] == index]:
            self._kick_pumps.pop(key).cancel()      # nobody there to forward chat to
        # The profiles it held are gone with it; what it last saved is on
        # disk. Forget this process's copies so the next read is the file.
        for key in [k for k, i in self._watching.items() if i == index]:
            self._watching.pop(key, None)
            uid, _, channel = key.rpartition(":")
            self._forget_profile(uid, channel)
        for key, rec in list(api._streams.items()):
            if not self._owns(index, rec.get("channel", "")):
                continue
            api.release_live_slot(key)
            if rec.get("status") == "paused":
                continue
            rec["status"] = "reconnecting"
            hit += 1
            await api.broadcast({"event": "stream_updated", "stream": rec},
                                user_id=rec.get("user_id") or None)
        log.error("ingest_worker_channels_interrupted", index=index, channels=hit)

    # ── what a worker asks, through src/ingestion/host.py ────────────────────

    async def h_broadcast(self, index: int, event: dict, user_id: str | None = None) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("event")
        if kind in ("stream_status", "stream_updated"):
            theirs = event.get("stream") if kind == "stream_updated" else event
            theirs = theirs if isinstance(theirs, dict) else {}
            channel = theirs.get("channel") or ""
            rec = api._streams.get(_key(channel, user_id))
            if rec is None:
                return                              # removed while the message was on its way
            if not self._owns(index, channel):
                log.warning("ingest_status_not_its_channel", index=index, kind=kind)
                return
            if rec.get("status") == "paused":
                if kind == "stream_status":
                    return                          # rule 2: nothing a worker says changes it
            else:
                if theirs.get("status"):
                    rec["status"] = theirs["status"]
                if kind == "stream_updated" and theirs.get("preset"):
                    rec["preset"] = theirs["preset"]
            if kind == "stream_updated":
                event = {**event, "stream": rec}    # rule 3
        elif kind == "profile_updated":
            # The worker saved it; keep this process's copy the same, so
            # GET /profiles/{channel} answers with what the detector has.
            self._remember_profile(user_id or "", event.get("profile"))
        await api.broadcast(event, user_id=user_id)

    def h_acquire(self, index: int, stream_key: str) -> bool:
        channel = _channel_of(stream_key)
        if not self._owns(index, channel):
            log.warning("ingest_slot_not_its_channel", index=index)
            return False
        rec = api._streams.get(stream_key)
        if rec is None or rec.get("status") == "paused":
            return False                            # gone, or paused: the stop is on its way
        return bool(api.acquire_live_slot(stream_key))

    def h_release(self, index: int, stream_key: str) -> None:
        if self._owns(index, _channel_of(stream_key)):
            api.release_live_slot(stream_key)

    def h_watchers(self, index: int, channel: str) -> list[str]:
        return list(api.watchers_of(channel))

    def h_room(self, index: int, uid: str) -> list[int]:
        used, room = api.suggestion_room(uid)
        return [int(used), int(room)]

    async def h_clip_ready(self, index: int, meta: dict):
        await api.notify_clip_ready(meta)
        return True

    async def h_file_ready(self, index: int, clip_id: str):
        await api.on_clip_file_ready(clip_id)
        return True

    async def h_trim(self, index: int) -> int:
        import asyncio
        from src.clips import files as clip_files
        return int(await asyncio.to_thread(clip_files.trim_to_cap) or 0)

    # ── profiles: held by the worker that is watching the channel ────────────

    def owns(self, uid: str, channel: str) -> bool:
        """Is this channel's profile in a worker right now? (api.py asks
        before every change to one.)"""
        index = self._watching.get(_key(channel, uid))
        if index is None:
            return False
        w = self.pool.workers[index]
        return bool(w.ready and w.link is not None and not w.link.closed)

    async def change(self, uid: str, channel: str, op: dict, platform: str = "twitch") -> dict | None:
        """Make one change (src/profiles/ops.py) where the profile is. If the
        worker cannot be reached it is made here instead, from what the
        worker last saved: the change is never dropped."""
        from src.ingestion.bridge import LinkClosed, RemoteError
        from src.profiles import ops
        key = _key(channel, uid)
        index = self._watching.get(key)
        link = self.pool.workers[index].link if index is not None else None
        if link is not None and not link.closed:
            try:
                res = await link.call("profile_apply", uid, channel, op, platform)
                if res:
                    self._remember_profile(uid, res.get("profile"))
                return res
            except (LinkClosed, RemoteError, TimeoutError) as exc:
                log.error("ingest_profile_change_fell_back", index=index, error=str(exc)[:160])
        self._watching.pop(key, None)
        self._forget_profile(uid, channel)
        return await ops.apply(uid or None, channel, op, platform)

    @staticmethod
    def _remember_profile(uid: str, data) -> None:
        if not isinstance(data, dict) or not data.get("channel"):
            return
        try:
            import copy
            from src.profiles.manager import get_profile_manager
            from src.profiles.profile import StreamerProfile
            # A COPY: from_dict removes keys from the dict it is given, and
            # this one goes on to the user's tabs as profile_updated.
            get_profile_manager(uid or None)._cache[data["channel"]] = \
                StreamerProfile.from_dict(copy.deepcopy(data))
        except Exception as exc:                    # noqa: BLE001
            log.warning("ingest_profile_copy_failed", error=repr(exc)[:160])

    @staticmethod
    def _forget_profile(uid: str, channel: str) -> None:
        try:
            from src.profiles.manager import get_profile_manager
            get_profile_manager(uid or None)._cache.pop(channel, None)
        except Exception:
            pass

    def h_stopped(self, index: int, channel: str, user_id: str = "", final=None) -> None:
        """The worker has finished stopping a channel and no longer holds its
        profile. `final` is the profile as it left it."""
        key = _key(channel, user_id)
        if self._watching.get(key) == index:
            self._watching.pop(key, None)
        if isinstance(final, dict):
            self._remember_profile(user_id, final)
        else:
            self._forget_profile(user_id, channel)

    # ── Kick chat: delivered here, wanted there ──────────────────────────────

    def h_kick_listen(self, index: int, broadcaster_id: str) -> None:
        """A worker's chat session wants this broadcaster's messages. Listen
        for them here like any session would (kick_events.listen) and pass
        each one on. Chat is droppable, as it is for a stalled session."""
        import asyncio
        from src.chat.platform import kick_events
        key = (index, str(broadcaster_id))
        if key in self._kick_pumps and not self._kick_pumps[key].done():
            return
        link = self.pool.workers[index].link

        async def pump() -> None:
            q = kick_events.listen(key[1])
            try:
                while True:
                    item = await q.get()
                    if item:
                        link.cast("kick_chat", key[1], item[0], item[1], droppable=True)
            finally:
                kick_events.unlisten(key[1], q)

        self._kick_pumps[key] = asyncio.create_task(pump(), name=f"kick-forward-{index}")

    def h_kick_unlisten(self, index: int, broadcaster_id: str) -> None:
        task = self._kick_pumps.pop((index, str(broadcaster_id)), None)
        if task is not None:
            task.cancel()

    async def h_kick_ensure(self, index: int, broadcaster_id: str, slug: str = "",
                            renew: bool = False) -> bool:
        from src.chat.platform import kick_events
        return bool(await kick_events.ensure_subscribed(str(broadcaster_id), slug, renew=bool(renew)))

    def h_log(self, index: int, event: str, level: str = "error", fields: dict | None = None) -> None:
        """A worker's error, counted as one of ours: src/alerts mails the
        owner from this process's tally."""
        from src import alerts
        alerts.note(str(event), "critical" if level == "critical" else "error",
                    {**(fields if isinstance(fields, dict) else {}), "ingest": index})
