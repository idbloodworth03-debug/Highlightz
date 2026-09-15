"""
Fetch a clip's video FROM TWITCH, for clips the live capture did not produce.

THIS IS THE GREY PATH, AND IT IS HERE ON PURPOSE. From July to September 2026
the product deliberately never pulled video out of Twitch: no CDN scraping, no
undocumented endpoints, and a test that failed if anyone tried. The owner
reversed that on 2026-09-15 with the risk laid out in front of them — Twitch's
Developer Agreement forbids storing Twitch content and the realistic downside
is the app's API access being pulled — because a clip that cannot be
downloaded, edited or scheduled is a clip the product cannot finish, and live
capture alone leaves every clip it missed and every clip from before capture
existed permanently stuck. That decision and its reasoning are recorded in
docs/HANDOFF.md; do not quietly restore the old constraint, and do not quietly
widen this one either.

HOW, AND WHY THIS WAY. The thumbnail-to-MP4 rewrite every blog post describes
is dead; `src/maintenance/probe_clip_media.py` proved that against prod in
July. What works is Twitch's playback-token GraphQL call, which is what every
clip tool alive uses. Streamlink implements it, is maintained against Twitch's
changes, and is already on the box as the capture engine — so this shells out
to streamlink exactly as the recorder does, and there is no hand-rolled
undocumented API call in this file for Twitch to change out from under us.

WHAT KEEPS IT NARROW.
  * Capture stays primary. It is free, it is better quality, and it never
    touches Twitch. This runs only when capture produced nothing.
  * ONE fetch at a time, process-wide (`_slot`) — the same rule captioning
    follows, for the same reason: this process also runs a streamlink+ffmpeg
    audio meter per monitored channel on one vCPU, and clip detection wins.
  * Per-clip dedupe (`_inflight`): a second request for a clip already being
    fetched joins the first rather than starting a second download.
  * Written to a `.part` name and renamed into place, so a half-fetched file
    can never read as "ready" — the store lists `.mp4` only.
  * Bounded: a timeout, a size ceiling, and the same disk cap and trim as
    every other file in the store.
  * OFF unless switched on. `clip_fetch_enabled` defaults to False like
    capture does; pulling bytes is a deliberate act, not a side effect of a
    deploy.
"""

import asyncio
import os
import re

import structlog

from config.settings import settings
from src.clips import files as clip_files

log = structlog.get_logger(__name__)

# Process-wide: never two streamlink downloads at once. See module docs.
_slot = asyncio.Semaphore(1)
# clip_id -> the task fetching it, so concurrent requests share one download.
_inflight: dict[str, asyncio.Task] = {}

# A Twitch clip slug: letters, digits, dash, underscore. It becomes one argv
# element (never a shell string) and the tail of a URL; this keeps it from
# being anything else.
_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]{1,120}$")

MB = 1024 * 1024


class FetchDisabled(RuntimeError):
    """clip_fetch_enabled is off."""


class FetchFailed(RuntimeError):
    """The download ran and produced nothing usable. The message is for the
    log and the operator, not the user."""


def enabled() -> bool:
    return bool(settings.clip_fetch_enabled)


def clip_url(slug: str) -> str | None:
    """The canonical clip page, which streamlink resolves to the video. Built
    from the slug rather than trusting a stored URL, so what reaches argv is
    exactly one shape."""
    if not slug or not _SLUG_RE.match(slug):
        return None
    return f"https://clips.twitch.tv/{slug}"


def fetchable(clip: dict) -> bool:
    """Whether this clip COULD be fetched: the feature is on, it is a Twitch
    clip, it carries a usable slug, and the broadcaster has not opted out.
    Says nothing about whether it already has a file — that is the store's
    question.

    THE OPT-OUT CHECK IS NOT OPTIONAL. The recorder refuses an opted-out
    channel before it starts, and the Privacy Policy says a channel that has
    opted out is never recorded. A file fetched from Twitch is the same thing
    to that broadcaster as a file recorded off their stream — video of them,
    held by us — so the promise has to hold here too, or it is not a promise.
    """
    from src.auth import optout
    if not enabled():
        return False
    if (clip.get("platform") or "twitch") != "twitch":
        return False
    if clip_url(clip.get("twitch_clip_id") or "") is None:
        return False
    try:
        if optout.is_opted_out(clip.get("channel") or ""):
            return False
    except Exception:
        # Cannot read the opt-out list: refuse rather than guess. Missing a
        # download is recoverable; holding video of someone who said no is not.
        return False
    return True


def in_flight(clip_id: str) -> bool:
    return clip_id in _inflight


async def fetch(clip_id: str, slug: str):
    """Fetch one clip. Returns the file path. Raises FetchDisabled or
    FetchFailed. A concurrent call for the same clip joins the download that
    is already running and gets the same result."""
    if not enabled():
        raise FetchDisabled("clip fetching is switched off")
    task = _inflight.get(clip_id)
    if task is None:
        task = asyncio.create_task(_run(clip_id, slug), name=f"fetch-{clip_id}")
        _inflight[clip_id] = task
        task.add_done_callback(lambda _t: _inflight.pop(clip_id, None))
    return await task


async def _run(clip_id: str, slug: str):
    url = clip_url(slug)
    out = clip_files.path_for(clip_id)
    if url is None or out is None:
        raise FetchFailed("not a fetchable clip")

    async with _slot:
        # Someone may have fetched or captured it while we waited for the slot.
        if clip_files.exists(clip_id):
            return out

        # Same cap and the same trim as every other file in the store; the
        # trim is in a thread because it walks the directory.
        if not clip_files.headroom_ok():
            await asyncio.to_thread(clip_files.trim_to_cap)
            if not clip_files.headroom_ok():
                raise FetchFailed("clip store is full")

        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_suffix(".fetch.part")
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                settings.streamlink_path,
                "--loglevel", "warning",
                "--force",                 # overwrite a stale .part
                "-o", str(part),
                url,
                "best",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, err = await asyncio.wait_for(
                    proc.communicate(), timeout=settings.clip_fetch_timeout_s)
            except asyncio.TimeoutError:
                # communicate() raising does not stop the child. Kill it, or
                # a stuck download keeps the slot's worth of bandwidth and a
                # streamlink process for as long as it likes.
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
                raise FetchFailed(f"timed out after {settings.clip_fetch_timeout_s}s")

            tail = (err or b"").decode(errors="replace")[-300:].strip()
            if proc.returncode != 0:
                raise FetchFailed(f"streamlink exited {proc.returncode}: {tail}")
            try:
                size = part.stat().st_size
            except OSError:
                size = 0
            if size == 0:
                raise FetchFailed(f"streamlink wrote nothing: {tail}")
            if size > settings.clip_fetch_max_mb * MB:
                raise FetchFailed(f"file is {size // MB} MB, over the "
                                  f"{settings.clip_fetch_max_mb} MB ceiling")

            # Atomic: the store lists .mp4, so until this rename nothing can
            # see a partial file as a finished one.
            os.replace(part, out)
            clip_files._forget_scan()
            log.info("clip_fetched", clip_id=clip_id, slug=slug,
                     size_mb=round(size / MB, 1))
            return out
        except FetchFailed as exc:
            log.warning("clip_fetch_failed", clip_id=clip_id, slug=slug,
                        error=str(exc))
            raise
        except Exception as exc:
            log.warning("clip_fetch_failed", clip_id=clip_id, slug=slug,
                        error=f"{type(exc).__name__}: {exc}")
            raise FetchFailed(str(exc)) from exc
        finally:
            try:
                part.unlink(missing_ok=True)
            except OSError:
                pass
