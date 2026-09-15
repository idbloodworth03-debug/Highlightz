"""Fetching a clip's video from Twitch when capture did not produce one.

WHAT THESE PIN. Not that streamlink works against Twitch — that cannot be
proven from here and is checked on prod by hand (see the deploy note). They pin
the SHAPE that keeps this grey path narrow: it is off by default, it runs one
download at a time, a second request for the same clip joins the first, a
partial file can never read as finished, and every failure leaves nothing
behind. The subprocess is faked; everything around it is real.
"""

import asyncio
import time

import pytest

from src.clips import fetch
from src.clips import files as clip_files


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    """Fresh store, fresh semaphore, fresh in-flight table, feature ON.

    The semaphore is rebuilt because asyncio binds it to the first loop that
    acquires it and each test runs its own loop; the in-flight table because
    a task left over from one test would be 'the download already running'
    in the next."""
    root = tmp_path / "clipfiles"
    root.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", root)
    clip_files._forget_scan()
    monkeypatch.setattr(fetch, "_slot", asyncio.Semaphore(1))
    monkeypatch.setattr(fetch, "_inflight", {})
    monkeypatch.setattr(fetch.settings, "clip_fetch_enabled", True)
    monkeypatch.setattr(fetch.settings, "clip_fetch_timeout_s", 5)
    monkeypatch.setattr(fetch.settings, "clip_fetch_max_mb", 200)
    return root


class _Proc:
    def __init__(self, *, rc=0, body=b"\0" * 4096, out=None, hang=False,
                 stderr=b"", delay=0.0):
        self.returncode = rc
        self._body, self._out, self._hang = body, out, hang
        self._stderr, self._delay = stderr, delay
        self.killed = False

    async def communicate(self):
        if self._hang:
            await asyncio.sleep(60)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._body is not None and self._out is not None:
            self._out.write_bytes(self._body)
        return b"", self._stderr

    def kill(self):
        self.killed = True

    async def wait(self):
        return self.returncode


def _fake_exec(monkeypatch, **proc_kw):
    """Install a fake streamlink. Returns the list of argv it was called with
    and the procs it produced."""
    calls, procs = [], []

    async def fake(*argv, **_kw):
        calls.append(list(argv))
        out = None
        if "-o" in argv:
            from pathlib import Path
            out = Path(argv[argv.index("-o") + 1])
        p = _Proc(out=out, **proc_kw)
        procs.append(p)
        return p

    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", fake)
    return calls, procs


def _run(coro):
    return asyncio.run(coro)


# ── off by default, and what "fetchable" means ───────────────────────────────

def test_it_is_off_unless_switched_on():
    """Same rule as capture: a feature that pulls bytes does not arrive
    switched on by a deploy."""
    from config.settings import Settings
    assert Settings().clip_fetch_enabled is False


def test_disabled_means_no_subprocess_ever_runs(monkeypatch):
    monkeypatch.setattr(fetch.settings, "clip_fetch_enabled", False)
    calls, _ = _fake_exec(monkeypatch)
    with pytest.raises(fetch.FetchDisabled):
        _run(fetch.fetch("c1", "GoodSlug"))
    assert calls == []


def test_fetchable_needs_the_feature_a_twitch_clip_and_a_slug(monkeypatch):
    ok = {"platform": "twitch", "twitch_clip_id": "AwkwardSlug-123_x"}
    assert fetch.fetchable(ok)
    assert not fetch.fetchable({**ok, "platform": "kick"})
    assert not fetch.fetchable({**ok, "twitch_clip_id": ""})
    assert not fetch.fetchable({"platform": "twitch"})
    monkeypatch.setattr(fetch.settings, "clip_fetch_enabled", False)
    assert not fetch.fetchable(ok)


def test_a_slug_that_is_not_a_slug_never_reaches_argv(monkeypatch):
    """One argv element, never a shell string — but a value that is not a slug
    is still refused before it becomes anything."""
    calls, _ = _fake_exec(monkeypatch)
    for bad in ("../etc", "a b", "x;rm", "", "ü"):
        assert fetch.clip_url(bad) is None
        with pytest.raises(fetch.FetchFailed):
            _run(fetch.fetch("c1", bad))
    assert calls == []


# ── the happy path ───────────────────────────────────────────────────────────

def test_a_fetched_clip_lands_in_the_store_under_its_clip_id(monkeypatch, store):
    calls, _ = _fake_exec(monkeypatch)
    path = _run(fetch.fetch("c1", "GoodSlug"))
    assert path == store / "c1.mp4" and path.is_file()
    assert clip_files.exists("c1")
    assert "c1" in clip_files.existing_ids(), "the listing cache was not dropped"


def test_streamlink_is_asked_for_the_clip_page_at_best_quality(monkeypatch):
    calls, _ = _fake_exec(monkeypatch)
    _run(fetch.fetch("c1", "GoodSlug"))
    argv = calls[0]
    assert argv[0] == fetch.settings.streamlink_path
    assert "https://clips.twitch.tv/GoodSlug" in argv
    assert argv[-1] == "best"


def test_it_writes_to_a_part_file_and_renames_into_place(monkeypatch, store):
    """The store lists .mp4 only, so until the rename nothing can see a
    half-written download as a finished clip."""
    calls, _ = _fake_exec(monkeypatch)
    _run(fetch.fetch("c1", "GoodSlug"))
    target = calls[0][calls[0].index("-o") + 1]
    assert target.endswith(".fetch.part") and not target.endswith(".mp4")
    assert not list(store.glob("*.part")), "a .part file was left behind"


def test_a_clip_already_on_disk_is_not_fetched_again(monkeypatch, store):
    (store / "c1.mp4").write_bytes(b"already here")
    calls, _ = _fake_exec(monkeypatch)
    path = _run(fetch.fetch("c1", "GoodSlug"))
    assert path.read_bytes() == b"already here"
    assert calls == []


# ── every failure leaves nothing behind ──────────────────────────────────────

def test_a_nonzero_exit_is_a_failure_with_no_file(monkeypatch, store):
    _fake_exec(monkeypatch, rc=1, body=b"partial", stderr=b"error: no clip")
    with pytest.raises(fetch.FetchFailed, match="exited 1"):
        _run(fetch.fetch("c1", "GoodSlug"))
    assert not list(store.iterdir())


def test_an_empty_download_is_a_failure(monkeypatch, store):
    _fake_exec(monkeypatch, rc=0, body=b"")
    with pytest.raises(fetch.FetchFailed, match="wrote nothing"):
        _run(fetch.fetch("c1", "GoodSlug"))
    assert not list(store.iterdir())


def test_an_oversized_download_is_rejected(monkeypatch, store):
    """A 30s clip is a few MB. Something far larger is not the clip."""
    monkeypatch.setattr(fetch.settings, "clip_fetch_max_mb", 1)
    _fake_exec(monkeypatch, body=b"\0" * (2 * fetch.MB))
    with pytest.raises(fetch.FetchFailed, match="ceiling"):
        _run(fetch.fetch("c1", "GoodSlug"))
    assert not list(store.iterdir())


def test_a_hung_download_is_killed_not_abandoned(monkeypatch, store):
    """communicate() raising does not stop the child. Without the kill a stuck
    streamlink keeps the bandwidth and the process for as long as it likes."""
    monkeypatch.setattr(fetch.settings, "clip_fetch_timeout_s", 0.05)
    _, procs = _fake_exec(monkeypatch, hang=True)
    with pytest.raises(fetch.FetchFailed, match="timed out"):
        _run(fetch.fetch("c1", "GoodSlug"))
    assert procs[0].killed
    assert not list(store.iterdir())


def test_a_full_store_makes_room_first_and_only_then_gives_up(monkeypatch, store):
    calls, _ = _fake_exec(monkeypatch)
    monkeypatch.setattr(clip_files, "headroom_ok", lambda: False)
    trimmed = {"n": 0}
    monkeypatch.setattr(clip_files, "trim_to_cap", lambda: trimmed.__setitem__("n", 1) or 0)
    with pytest.raises(fetch.FetchFailed, match="full"):
        _run(fetch.fetch("c1", "GoodSlug"))
    assert trimmed["n"] == 1, "it gave up without trying to make room"
    assert calls == []


# ── one at a time, and shared ────────────────────────────────────────────────

def test_two_requests_for_one_clip_share_one_download(monkeypatch):
    calls, _ = _fake_exec(monkeypatch, delay=0.05)

    async def both():
        return await asyncio.gather(fetch.fetch("c1", "GoodSlug"),
                                    fetch.fetch("c1", "GoodSlug"))

    a, b = _run(both())
    assert a == b
    assert len(calls) == 1, "the same clip was downloaded twice"


def test_different_clips_are_downloaded_one_at_a_time(monkeypatch):
    """This process also runs an audio meter per monitored channel on one
    vCPU. Two concurrent downloads would starve them for no gain."""
    live = {"now": 0, "peak": 0}

    async def fake(*argv, **_kw):
        from pathlib import Path
        out = Path(argv[argv.index("-o") + 1])
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        p = _Proc(out=out, delay=0.03)
        orig = p.communicate

        async def communicate():
            try:
                return await orig()
            finally:
                live["now"] -= 1
        p.communicate = communicate
        return p

    monkeypatch.setattr(fetch.asyncio, "create_subprocess_exec", fake)

    async def three():
        await asyncio.gather(fetch.fetch("c1", "S1"), fetch.fetch("c2", "S2"),
                             fetch.fetch("c3", "S3"))

    _run(three())
    assert live["peak"] == 1, f"{live['peak']} downloads ran at once"


def test_a_failure_is_shared_by_everyone_waiting_and_then_forgotten(monkeypatch):
    """Joiners get the same answer, and the next request after a failure
    starts a fresh attempt rather than re-raising a dead one."""
    calls, _ = _fake_exec(monkeypatch, rc=1, delay=0.02)

    async def both():
        r = await asyncio.gather(fetch.fetch("c1", "S"), fetch.fetch("c1", "S"),
                                 return_exceptions=True)
        return r

    a, b = _run(both())
    assert isinstance(a, fetch.FetchFailed) and isinstance(b, fetch.FetchFailed)
    assert len(calls) == 1
    assert not fetch.in_flight("c1")
    with pytest.raises(fetch.FetchFailed):
        _run(fetch.fetch("c1", "S"))
    assert len(calls) == 2, "a second attempt did not run"


def test_an_opted_out_broadcaster_is_never_fetched(monkeypatch):
    """The Privacy Policy says an opted-out channel is never recorded. A file
    fetched from Twitch is the same thing to that broadcaster — video of them,
    held by us — so the promise has to hold here or it is not a promise."""
    from src.auth import optout
    clip = {"platform": "twitch", "twitch_clip_id": "Slug", "channel": "nope"}
    monkeypatch.setattr(optout, "is_opted_out", lambda login: login == "nope")
    assert not fetch.fetchable(clip)
    assert fetch.fetchable({**clip, "channel": "fine"})


def test_an_unreadable_opt_out_list_refuses_rather_than_guesses(monkeypatch):
    from src.auth import optout
    monkeypatch.setattr(optout, "is_opted_out",
                        lambda login: (_ for _ in ()).throw(OSError("disk")))
    assert not fetch.fetchable({"platform": "twitch", "twitch_clip_id": "S",
                                "channel": "c"})
