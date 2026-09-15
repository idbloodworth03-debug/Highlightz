"""Every clip says whether it can be downloaded, and why not when it cannot.

THE BUG THIS CLOSES IS AN ABSENCE, WHICH IS WHY IT WAS HARD TO SEE. The
download worked; it just never appeared. A clip with no captured file rendered
no button at all, and from the outside "there is no file for this clip" and
"the download feature is broken" look exactly the same. On top of that the
button that DID render was a 13px glyph with no word beside it, in a row where
every other control was labelled.

So the fix has two halves and both are pinned here:

  * the server says WHICH of the five states a clip is in, rather than a
    boolean that collapses "not yet", "never" and "not any more" into the same
    silence, and
  * the browser always answers — with the download, with "preparing", or with
    the reason there is no file.

THE ONE THING THAT MUST NOT CREEP IN. Nothing in the no-file copy may send the
USER to Twitch to get the video themselves. These clips are of OTHER people's
channels; Twitch's download is a broadcaster control in their own Creator
Dashboard, so that advice points a clipper at a button that does not exist for
them. (Since 2026-09-15 the PRODUCT fetches the file from Twitch on request —
that is a different sentence, and the copy says it.)
"""

import time

import pytest

from src.dashboard import api

FRONTEND = __import__("pathlib").Path(__file__).resolve().parent.parent / "src/dashboard/aurora_html.py"
SRC = FRONTEND.read_text()


@pytest.fixture
def on_disk(tmp_path, monkeypatch):
    """A real clipfiles root, so `exists()` is answered by the filesystem the
    way it is in production rather than by a stub."""
    from src.clips import files as clip_files
    root = tmp_path / "clipfiles"
    root.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", root)
    return root


def _clip(cid="c1", age_s=0.0):
    return {"id": cid, "user_id": "u1", "channel": "aceu",
            "created_at": time.time() - age_s}


# ── the four states ──────────────────────────────────────────────────────────

def test_a_captured_clip_is_ready(on_disk, monkeypatch):
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    (on_disk / "c1.mp4").write_bytes(b"x")
    out = api._clip_out(_clip())
    assert out["file_state"] == "ready" and out["has_file"] is True


def test_a_fresh_clip_is_pending_not_missing(on_disk, monkeypatch):
    """The cut runs AFTER the moment's tail has been broadcast and buffered, so
    the card exists for ~20s before its file does. Calling that 'no file' is
    what taught people the feature does not work."""
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    out = api._clip_out(_clip(age_s=5))
    assert out["file_state"] == "pending" and out["has_file"] is False


def test_an_old_clip_with_no_file_is_missed(on_disk, monkeypatch):
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    out = api._clip_out(_clip(age_s=api._CLIP_FILE_WAIT_S + 60))
    assert out["file_state"] == "missed"


def test_capture_switched_off_says_so_rather_than_missed(on_disk, monkeypatch):
    """A different sentence to the user and a different fix for the operator:
    'off' is a flag, 'missed' is a buffer that did not cover the moment."""
    monkeypatch.setattr(api.settings, "clip_capture_enabled", False)
    assert api._clip_out(_clip(age_s=1))["file_state"] == "off"


def test_the_wait_window_is_longer_than_the_cut_takes():
    """`_cut_local_file` sleeps post_roll + two segments before it can even
    start, so a window near that length would call files missing while they are
    still being written."""
    from config.settings import settings as s
    earliest = s.clip_post_roll_seconds + s.clip_capture_segment_s * 2 + 1
    assert api._CLIP_FILE_WAIT_S > earliest * 2


def test_has_file_still_means_what_it_meant(on_disk, monkeypatch):
    """Every existing caller reads the boolean. It has to keep agreeing with
    the filesystem exactly."""
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    assert api._clip_out(_clip())["has_file"] is False
    (on_disk / "c1.mp4").write_bytes(b"x")
    assert api._clip_out(_clip())["has_file"] is True


def test_a_broken_clip_store_does_not_break_the_clip_list(monkeypatch):
    """Serialising a clip must not raise because the disk answered oddly — the
    whole review queue is rendered from this."""
    from src.clips import files as clip_files
    monkeypatch.setattr(clip_files, "exists",
                        lambda cid: (_ for _ in ()).throw(OSError("nope")))
    assert api._clip_out(_clip())["has_file"] is False


# ── the offer in the browser ─────────────────────────────────────────────────

def _card():
    return SRC[SRC.index("const fileState ="):SRC.index("const edBtn")]


def test_the_download_button_has_the_word_on_it():
    """It was an unlabelled 13px glyph among labelled buttons. Findable only if
    you already knew it was there, which is not the same as being offered."""
    assert ">Download</a>" in _card()


def test_a_clip_still_being_cut_says_preparing():
    card = _card()
    assert "pending" in card and "Preparing" in card


def test_the_card_offers_nothing_when_there_is_no_file():
    """A grid of dead controls is noise — the modal is where the reason goes."""
    assert ") : null;" in _card(), \
        "the card renders something for a clip with no file"


def _modal():
    """The modal's download block, ending at the approve/reject row — the one
    landmark below it that does not change shape when the buttons above do."""
    i = SRC.index("clip.file_state === 'pending'")
    return SRC[i:SRC.index("clip.status==='pending' && <div className=\"rd-modal-actions\"", i)]


def test_the_modal_always_answers():
    """This is the screen someone opens because they want the file, so 'no
    button' is the one answer it must never give."""
    modal = _modal()
    assert "file?download=1" in modal, "no download for a ready clip"
    assert "Preparing the download" in modal, "nothing for a clip still being cut"
    assert modal.count("No file for this clip") == 2, \
        "'off' and 'missed' do not both explain themselves"


def test_the_two_no_file_reasons_read_differently():
    """They have different causes and different outlooks — collapsing them
    would tell someone their buffer missed when the feature is simply off."""
    modal = _modal()
    assert "not holding video" in modal          # off
    assert "buffer did not cover" in modal       # missed


def test_no_copy_sends_the_user_to_twitch_for_the_file():
    """See the module docstring: a clipper has no download button on another
    broadcaster's clip, and this is the edge of the compliance line."""
    modal = _modal().lower()
    for bad in ("creator dashboard", "download it from twitch",
                "download from twitch", "get it from twitch"):
        assert bad not in modal


def test_the_ready_event_clears_the_preparing_state():
    """Patching has_file alone would leave file_state at 'pending', so the card
    would keep saying Preparing over a file that is on disk."""
    i = SRC.index("msg.event==='clip_file_ready'")
    branch = SRC[i:i + 500]
    assert "file_state:'ready'" in branch and "has_file:true" in branch


# ── the diagnosis (why a file is missing at all) ─────────────────────────────

def test_every_give_up_in_the_cut_path_logs_why():
    """Two of the four exits logged nothing, so 'no clips are downloadable'
    arrived with no trace of which one happened — and no way to tell it from a
    UI that was not rendering the button."""
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker.StreamWorker._cut_local_file)
    body = src[src.index("rec = self._recorder"):]
    # Every bare `return` in the give-up section must be preceded by a log.
    for why in ("no_recorder", "worker_stopped", "no_headroom", "bad_clip_id",
                "cut_raised", "buffer_miss"):
        assert why in body, f"the {why} exit is still silent"
    # The real invariant, and what catches an exit added later: every way out
    # of the give-up section is explained. Counted rather than eyeballed
    # because a silent `return` is invisible in review — that is how two of
    # them got there.
    gave_up = body[:body.index("log.info(\"clip_file_ready\"")]
    assert gave_up.count("return") == gave_up.count('"clip_file_skipped"'), \
        "an exit path gives up without saying why"


def test_the_reasons_share_one_event_name():
    """One grep has to answer the question for the whole class, or nobody will
    find the ones they did not think to look for."""
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker.StreamWorker._cut_local_file)
    assert src.count('"clip_file_skipped"') == 5


def test_the_diagnostic_exists_and_is_read_only():
    """It is pointed at production, where a tool that writes is a tool nobody
    dares run."""
    import inspect
    from src.maintenance import why_no_download
    src = inspect.getsource(why_no_download)
    for writes in ("write_text(", "unlink(", "_save(", "rmtree("):
        assert writes not in src, f"the diagnostic calls {writes}"
    assert "clip_capture_enabled" in src and "clip_file_skipped" in src


# ── the size cap must not become a wall ──────────────────────────────────────

def _store(tmp_path, monkeypatch, cap_mb):
    from src.clips import files as clip_files
    root = tmp_path / "clipfiles"
    root.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", root)
    monkeypatch.setattr(clip_files.settings, "clip_file_max_total_mb", cap_mb)
    return clip_files, root


def _put(root, name, mb, age_s):
    p = root / (name + ".mp4")
    p.write_bytes(b"\0" * int(mb * 1024 * 1024))
    import os
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


def test_a_full_store_evicts_the_oldest_instead_of_refusing(tmp_path, monkeypatch):
    """The cap used to stop every cut until the 30-day clock freed something,
    so a store that filled early quietly stopped producing downloads — with
    symptoms identical to the bug that started all this."""
    clip_files, root = _store(tmp_path, monkeypatch, cap_mb=10)
    for i in range(10):
        _put(root, f"c{i}", mb=1, age_s=86400 * (10 - i))
    assert clip_files.headroom_ok() is False
    assert clip_files.trim_to_cap() > 0
    assert clip_files.headroom_ok() is True
    assert not (root / "c0.mp4").exists(), "the oldest file survived the trim"
    assert (root / "c9.mp4").exists(), "the newest file was evicted"


def test_the_trim_leaves_headroom_rather_than_stopping_at_the_cap(tmp_path, monkeypatch):
    """Trimming to exactly the cap puts the next cut back at the wall, so every
    clip from then on pays for a trim."""
    clip_files, root = _store(tmp_path, monkeypatch, cap_mb=10)
    for i in range(10):
        _put(root, f"c{i}", mb=1, age_s=86400 * (10 - i))
    clip_files.trim_to_cap()
    assert clip_files.total_bytes() <= 10 * clip_files.MB * clip_files._TRIM_TARGET


def test_a_file_someone_may_be_about_to_download_is_never_evicted(tmp_path, monkeypatch):
    """Deleting a clip that landed minutes ago to store the next one trades a
    certain loss for a speculative gain."""
    clip_files, root = _store(tmp_path, monkeypatch, cap_mb=4)
    for i in range(5):
        _put(root, f"new{i}", mb=1, age_s=60)
    assert clip_files.trim_to_cap() == 0
    assert len(list(root.glob("*.mp4"))) == 5


def test_an_under_cap_store_is_left_alone(tmp_path, monkeypatch):
    clip_files, root = _store(tmp_path, monkeypatch, cap_mb=100)
    _put(root, "c1", mb=1, age_s=86400)
    assert clip_files.trim_to_cap() == 0
    assert (root / "c1.mp4").exists()


def test_no_cap_means_no_eviction(tmp_path, monkeypatch):
    """0 disables the cap, and a disabled cap must not delete anything."""
    clip_files, root = _store(tmp_path, monkeypatch, cap_mb=0)
    _put(root, "c1", mb=1, age_s=86400 * 99)
    assert clip_files.trim_to_cap() == 0
    assert (root / "c1.mp4").exists()


def test_the_cut_path_makes_room_before_giving_up():
    """Reaching the cap must not be the end of downloads until the next sweep,
    six hours away."""
    import inspect
    from src.ingestion import stream_worker
    src = inspect.getsource(stream_worker.StreamWorker._cut_local_file)
    i = src.index("headroom_ok()")
    window = src[i:i + 600]
    assert "trim_to_cap" in window, "the cut still gives up at the cap"
    assert "to_thread" in window, \
        "a directory walk and a run of unlinks would run on the meters' core"


def test_the_periodic_sweep_also_trims_by_size():
    """Age alone never reaches a store that fills before its files expire."""
    import inspect
    from src import main
    assert "trim_to_cap" in inspect.getsource(main.auto_delete_old_clips) or \
        "trim_to_cap" in inspect.getsource(main.sweep_dead_clips_task)


# ── an evicted file is not a buffer miss ─────────────────────────────────────

def test_a_clip_older_than_everything_we_hold_reads_as_expired(tmp_path, monkeypatch):
    """The size cap deletes oldest-first, so on a busy box a file survives days,
    not the configured 30. Blaming 'the buffer did not cover this moment' for a
    file the cap cleared tells the user to expect a fix that is not coming."""
    from src.clips import files as clip_files
    root = tmp_path / "clipfiles"
    root.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", root)
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    _put(root, "kept", mb=0, age_s=86400)          # the oldest we still hold
    assert api._clip_out(_clip("gone", age_s=86400 * 3))["file_state"] == "expired"


def test_a_recent_clip_with_no_file_is_still_a_miss(tmp_path, monkeypatch):
    """Inside the horizon the cap is not the explanation, so the honest answer
    is still that nothing was captured for that moment."""
    from src.clips import files as clip_files
    root = tmp_path / "clipfiles"
    root.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", root)
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    _put(root, "kept", mb=0, age_s=86400 * 3)
    assert api._clip_out(_clip("nope", age_s=3600))["file_state"] == "missed"


def test_an_empty_store_never_claims_a_clip_expired(on_disk, monkeypatch):
    """With nothing on disk there is no horizon to compare against, and
    guessing 'expired' would invent a file that never existed."""
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    assert api._clip_out(_clip(age_s=86400 * 9))["file_state"] == "missed"


def test_the_oldest_mtime_is_the_oldest(tmp_path, monkeypatch):
    from src.clips import files as clip_files
    root = tmp_path / "clipfiles"
    root.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", root)
    assert clip_files.oldest_mtime() == 0.0
    _put(root, "young", mb=0, age_s=60)
    old = _put(root, "old", mb=0, age_s=86400)
    assert abs(clip_files.oldest_mtime() - old.stat().st_mtime) < 0.01


def test_the_expired_copy_does_not_blame_the_buffer():
    """The whole reason this state exists."""
    modal = _modal()
    i = modal.index("This download has expired")
    branch = modal[i:i + 420]
    assert "buffer" not in branch, \
        "the expired copy blames a buffer miss for a file the cap cleared"
    assert "make room" in branch, "it does not say what actually happened"


# ── serialising a clip list must not walk the disk per clip ──────────────────

def test_serialising_a_big_clip_list_does_not_rescan_the_store(tmp_path, monkeypatch):
    """THE REGRESSION THIS EXISTS FOR. `_clip_out` runs once per clip, and the
    horizon lookup it gained walked the whole clipfiles directory. At
    production size — ~1,600 clips against ~900 files — that is hundreds of
    thousands of stat() calls per GET /clips, synchronously, on the event loop,
    on one vCPU. It did not fail: it took the dashboard, the socket and the
    clip processor down with it, which from outside reads as clips erroring and
    nothing being taken.

    Counted rather than timed, because a timing assertion on a shared CI box is
    a flake generator and the thing that actually matters is the number of
    scans, not how long one took.
    """
    from src.clips import files as clip_files
    root = tmp_path / "clipfiles"
    root.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", root)
    clip_files._forget_scan()
    monkeypatch.setattr(api.settings, "clip_capture_enabled", True)
    for i in range(20):
        _put(root, f"f{i}", mb=0, age_s=86400)

    scans = {"n": 0}
    real_glob = type(root).glob

    def counting_glob(self, pattern):
        if str(self) == str(root):
            scans["n"] += 1
        return real_glob(self, pattern)

    monkeypatch.setattr(type(root), "glob", counting_glob)
    import os as _os
    real_scandir = _os.scandir
    def counting_scandir(path=".", *a, **k):
        if str(path) == str(root):
            scans["n"] += 1
        return real_scandir(path, *a, **k)
    monkeypatch.setattr(clip_files.os, "scandir", counting_scandir)

    # Per-clip stat() is the same bug one order of magnitude smaller, so it is
    # counted here too: 1,606 is_file()+stat() pairs was 340 ms per request.
    stats = {"n": 0}
    real_stat = type(root).stat
    def counting_stat(self, *a, **k):
        if str(self).startswith(str(root)):
            stats["n"] += 1
        return real_stat(self, *a, **k)
    monkeypatch.setattr(type(root), "stat", counting_stat)

    listing = clip_files.existing_ids()
    for i in range(200):
        api._clip_out(_clip(f"absent{i}", age_s=86400 * 5), listing)
    assert scans["n"] <= 1, (
        f"the store was scanned {scans['n']} times to serialise 200 clips — "
        "this is the one that took production down")
    assert stats["n"] == 0, (
        f"{stats['n']} per-clip stat() calls to serialise 200 clips — "
        "the listing is not being used")


def test_the_clip_list_endpoint_reads_the_directory_once(tmp_path, monkeypatch):
    """The guard one level up: it is `list_clips` that has to pass the listing
    down, and a future caller that forgets reverts the whole fix silently."""
    import inspect
    src = inspect.getsource(api.list_clips)
    assert "existing_ids()" in src, "the clip list does not read the store once"
    assert "_clip_out(c, listing)" in src, "the listing is computed and not used"


def test_a_single_clip_is_checked_exactly_not_from_the_cache():
    """The cached listing is for lists. One clip gets the authoritative stat,
    so approving a clip cannot report a file state up to the TTL out of date."""
    import inspect
    src = inspect.getsource(api._file_state)
    assert "clip_files.exists(" in src, \
        "the single-clip path no longer checks the real file"


def test_the_file_endpoint_never_trusts_the_cache():
    """It is about to hand over bytes. A cache cannot be allowed to say a file
    is there when it is not — that serves a 200 with nothing behind it."""
    import inspect
    src = inspect.getsource(api.get_clip_file)
    assert "existing_ids" not in src
    assert "path.is_file()" in src


def test_the_horizon_cache_is_not_shared_between_stores(tmp_path, monkeypatch):
    """Keyed on the root as well as the clock. Without that a cached answer
    outlives the directory it was computed from — which in tests means one
    test's store answering for the next."""
    from src.clips import files as clip_files
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir(); b.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", a)
    _put(a, "old", mb=0, age_s=86400)
    assert clip_files.oldest_mtime() > 0
    monkeypatch.setattr(clip_files, "_ROOT", b)
    assert clip_files.oldest_mtime() == 0.0, "an empty store answered with another's"


def test_deleting_files_drops_the_cached_horizon(tmp_path, monkeypatch):
    """A trim moves the horizon by definition. A minute of staleness after one
    would mislabel clips that are still downloadable as expired."""
    from src.clips import files as clip_files
    root = tmp_path / "clipfiles"
    root.mkdir()
    monkeypatch.setattr(clip_files, "_ROOT", root)
    monkeypatch.setattr(clip_files.settings, "clip_file_max_total_mb", 10)
    clip_files._forget_scan()
    for i in range(10):
        _put(root, f"c{i}", mb=1, age_s=86400 * (10 - i))
    first = clip_files.oldest_mtime()
    assert clip_files.trim_to_cap() > 0
    assert clip_files.oldest_mtime() > first, "the horizon is stale after a trim"


# ── fetching from Twitch, as the browser offers it ───────────────────────────

def test_a_fetchable_clip_gets_a_download_button_on_the_card():
    """'fetchable' is what turns "no file" from an explanation into a button."""
    card = _card()
    assert "clip.fetchable ?" in card
    assert "hz_fetch_clip" in card, "the card cannot ask for a fetch"
    assert "download:true" in card, "pressing Download would not download"


def test_a_fetch_in_progress_is_shown_and_not_pressable():
    card = _card()
    assert "fileState === 'fetching'" in card and "Fetching" in card


def test_edit_is_offered_for_a_fetchable_clip():
    """The editor route fetches inline, so Edit is one click either way."""
    i = SRC.index("const edBtn")
    assert "(clip.has_file || clip.fetchable) && onEdit" in SRC[i:i + 200]
    assert "(clip.has_file || clip.fetchable) && onEdit && <button" in _modal()


def test_the_modal_offers_the_fetch_too():
    modal = _modal()
    assert "clip.fetchable" in modal and "hz_fetch_clip" in modal
    assert "Getting the video from Twitch" in modal


def test_app_owns_the_fetch_and_the_download_that_follows():
    """One listener, so the clip state, the request and the auto-download live
    in one place rather than in every card."""
    i = SRC.index("hz_fetch_clip', onFetch")
    app = SRC[SRC.rindex("const wantDownload", 0, i):i]
    assert "/clips/${id}/fetch" in app
    assert "setFileState(id, 'fetching')" in app, "no optimistic state — double presses"
    assert "setFileState(id, 'missed')" in app, "a failed request leaves it stuck on Fetching"


def test_a_fetched_file_starts_the_download_the_person_asked_for():
    i = SRC.index("msg.event==='clip_file_ready'")
    branch = SRC[i:i + 900]
    assert "wantDownload.current.delete(msg.clip_id)" in branch
    assert "file?download=1" in branch, "the person has to press Download twice"


def test_a_failed_fetch_has_a_handler_that_puts_the_button_back():
    """An event with no branch is silently dropped; a branch that does not
    reset the state leaves the card on Fetching forever."""
    i = SRC.index("msg.event==='clip_fetch_failed'")
    branch = SRC[i:i + 400]
    assert "setFileState(msg.clip_id, 'missed')" in branch
    assert "flash(" in branch
