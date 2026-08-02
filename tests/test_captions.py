"""
Auto-captions on the droplet.

The owner chose to run Whisper here rather than pay for an API, so the whole
risk of this feature is ONE thing: it shares a single CPU core with an audio
meter per monitored channel. Clip detection is the product; captions are a
convenience. Every test below is about making sure captioning can never win
that fight.

The Whisper call itself is stubbed — the dev container's egress proxy blocks
the weights host, so it has never run here. `python -m src.captions.transcribe
--selftest` is the prod check for that one function.
"""

import asyncio
import json
import pathlib
import time

import pytest

from src.captions import transcribe as cap
from config.settings import settings

# Captured before the autouse fixture replaces it, so the one test that
# needs the real implementation can still reach it.
REAL_RUN_WHISPER = cap._run_whisper


@pytest.fixture(autouse=True)
def no_real_whisper(monkeypatch, tmp_path):
    """Replace the model call and the ffmpeg step. Everything else is real."""
    monkeypatch.setattr(cap, "extract_audio",
                        lambda v, out, timeout=60.0: out.write_bytes(b"RIFFfake"))
    monkeypatch.setattr(cap, "_run_whisper",
                        lambda wav: ([cap.Segment(0.0, 1.5, "insane clutch")], "en"))
    cap._model = None
    yield


def a_video(tmp_path, name="clip.mp4"):
    v = tmp_path / name
    v.write_bytes(b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64)
    return v


# ── The core safety property ──────────────────────────────────────────────────

def test_only_one_clip_is_transcribed_at_a_time(tmp_path, monkeypatch):
    """Two concurrent runs on a single core make BOTH slow and starve the live
    audio meters. The semaphore is process-wide, not per-user, precisely
    because the core is shared by everyone."""
    overlap = {"now": 0, "max": 0}

    def slow(wav):
        overlap["now"] += 1
        overlap["max"] = max(overlap["max"], overlap["now"])
        time.sleep(0.15)
        overlap["now"] -= 1
        return ([cap.Segment(0.0, 1.0, "hi")], "en")

    monkeypatch.setattr(cap, "_run_whisper", slow)

    async def go():
        vids = [a_video(tmp_path, f"c{i}.mp4") for i in range(4)]
        await asyncio.gather(*(cap.transcribe(v) for v in vids))
    asyncio.run(go())

    assert overlap["max"] == 1, (
        f"{overlap['max']} transcriptions ran at once — on 1 vCPU that starves "
        f"clip detection, which is the whole product")


def test_a_hung_transcription_cannot_pin_the_core_forever(tmp_path, monkeypatch):
    """Without a timeout a pathological file holds the only core indefinitely
    and every monitored channel goes deaf behind it."""
    monkeypatch.setattr(settings, "captions_timeout_s", 0.2)
    monkeypatch.setattr(cap, "_run_whisper", lambda wav: time.sleep(5))

    with pytest.raises(RuntimeError) as e:
        asyncio.run(cap.transcribe(a_video(tmp_path)))
    assert "longer than" in str(e.value)


def test_the_slot_is_released_after_a_failure(tmp_path, monkeypatch):
    """A crash that leaked the semaphore would wedge captioning permanently —
    every later request would block forever with no error."""
    monkeypatch.setattr(cap, "_run_whisper",
                        lambda wav: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        asyncio.run(cap.transcribe(a_video(tmp_path)))
    assert not cap._slot.locked(), "semaphore leaked — captioning is now wedged"

    # And it still works afterwards.
    monkeypatch.setattr(cap, "_run_whisper",
                        lambda wav: ([cap.Segment(0, 1, "ok")], "en"))
    assert asyncio.run(cap.transcribe(a_video(tmp_path)))["segments"]


def test_the_temp_wav_is_always_cleaned_up(tmp_path, monkeypatch):
    """Audio extraction writes a WAV beside the video. Leaking one per attempt
    fills a 50 GB disk that clipping and billing also depend on."""
    monkeypatch.setattr(cap, "_run_whisper",
                        lambda wav: (_ for _ in ()).throw(RuntimeError("boom")))
    v = a_video(tmp_path)
    with pytest.raises(RuntimeError):
        asyncio.run(cap.transcribe(v))
    assert not list(tmp_path.glob("*.wav")), "temp audio left on disk after a failure"


# ── Storage ───────────────────────────────────────────────────────────────────

def test_captions_sit_beside_their_video_so_deleting_it_takes_them_too(tmp_path):
    v = a_video(tmp_path)
    assert cap.captions_path(v).parent == v.parent
    assert v.name in cap.captions_path(v).name


def test_save_is_atomic_and_load_round_trips(tmp_path):
    v = a_video(tmp_path)
    payload = asyncio.run(cap.transcribe(v))
    cap.save(v, payload)
    assert json.loads(cap.captions_path(v).read_text())      # complete JSON
    assert not list(tmp_path.glob("*.tmp")), "temp file left behind"
    assert cap.load(v)["segments"][0]["text"] == "insane clutch"


def test_missing_or_corrupt_captions_read_as_none_not_a_crash(tmp_path):
    v = a_video(tmp_path)
    assert cap.load(v) is None
    cap.captions_path(v).write_text("{not json")
    assert cap.load(v) is None, "a truncated write must not break opening the editor"


def test_a_missing_video_fails_clearly(tmp_path):
    with pytest.raises(RuntimeError) as e:
        asyncio.run(cap.transcribe(tmp_path / "gone.mp4"))
    assert "no longer on disk" in str(e.value)


def test_blank_segments_are_dropped_by_the_real_filter(monkeypatch):
    """Whisper emits blank/whitespace segments over silence. Rendering those
    would flash an empty caption box on screen mid-clip.

    Exercises the REAL _run_whisper (the autouse fixture stubs it for every
    other test, so it is captured at import and restored here) with only the
    model itself faked.
    """
    class _Seg:
        def __init__(self, t): self.start, self.end, self.text = 0.0, 1.0, t

    class _Model:
        @staticmethod
        def transcribe(*a, **k):
            return ([_Seg("hi"), _Seg("   "), _Seg(""), _Seg(None), _Seg(" there ")],
                    type("Info", (), {"language": "en"})())

    monkeypatch.setattr(cap, "_model", _Model())
    segs, lang = REAL_RUN_WHISPER(pathlib.Path("/tmp/x.wav"))
    assert [s.text for s in segs] == ["hi", "there"], "blanks not dropped or not trimmed"
    assert lang == "en"


class _W:
    def __init__(self, start, end, word):
        self.start, self.end, self.word = start, end, word


class _WSeg:
    def __init__(self, start, end, text, words=None):
        self.start, self.end, self.text, self.words = start, end, text, words


def test_one_long_segment_is_broken_into_cues_that_actually_turn_over():
    """THE BUG THIS EXISTS FOR: Whisper returns sentence-level segments, so a
    clip of someone talking without pausing came back as ONE segment spanning
    the whole clip — a single caption that never changed for 30 seconds. The
    transcript was right and the captions were useless."""
    words = [_W(i * 0.4, i * 0.4 + 0.4, f"w{i}") for i in range(12)]
    seg = _WSeg(0.0, 4.8, " ".join(w.word for w in words), words)

    cues = cap._cues_from_words([seg])

    assert len(cues) >= 3, f"still one blob: {cues}"
    for c in cues:
        assert len(c.text.split()) <= cap._MAX_CUE_WORDS
        assert c.end - c.start <= cap._MAX_CUE_S + 0.5
    # Every word survives, in order — shortening cues must not drop speech.
    assert " ".join(c.text for c in cues) == seg.text


def test_a_pause_ends_a_cue_instead_of_being_spanned():
    """A cue whose timing spans a long silence sits on screen over nothing."""
    cues = cap._cues_from_words([_WSeg(0.0, 9.0, "a b", [
        _W(0.0, 0.3, "a"), _W(8.0, 8.3, "b")])])
    assert len(cues) == 2, "a 7.7s gap was swallowed into one cue"
    assert cues[0].end < 1.0 and cues[1].start > 7.0


def test_a_segment_with_no_word_timings_is_kept_whole_not_guessed_at():
    """Better one long true caption than invented timings."""
    cues = cap._cues_from_words([_WSeg(1.0, 4.0, "  no words here  ", None)])
    assert [(c.start, c.end, c.text) for c in cues] == [(1.0, 4.0, "no words here")]


def test_word_timestamps_are_requested_from_whisper():
    """Guard on the flag itself: without it faster-whisper returns no `words`,
    every segment takes the keep-whole fallback, and the one-blob bug is back
    with all the cue-shaping code still present and silently doing nothing."""
    import inspect
    # The autouse fixture replaces _run_whisper, so read the one captured at
    # import — otherwise this inspects the stub and passes on nothing.
    assert "word_timestamps=True" in inspect.getsource(REAL_RUN_WHISPER)


def test_vad_is_off_by_default_and_wired_to_the_setting():
    """VAD discards audio BEFORE transcription, so a false negative deletes
    speech with nothing downstream able to recover it. On prod it kept 8.07s
    of a 30.01s clip and cut the sentence in half. Off by default; the setting
    exists so it can be put back without a deploy."""
    import inspect
    assert settings.captions_vad is False, "VAD back on by default — it ate speech"
    src = inspect.getsource(REAL_RUN_WHISPER)
    assert "vad_filter=settings.captions_vad if vad is None else vad" in src, \
        "vad_filter hard-coded again; the setting would do nothing"


def test_the_model_is_configured_for_a_single_core_box():
    """cpu_threads must be pinned. Left to itself CTranslate2 takes every core
    it can see — on this box that is the only one, and the audio meters lose."""
    import inspect
    src = inspect.getsource(cap._load_model)
    assert "cpu_threads=1" in src, "Whisper would grab the only core"
    assert 'compute_type="int8"' in src, "float32 on a 2 GB box is asking for OOM"


def test_finished_caption_jobs_do_not_accumulate_forever():
    """One entry per caption ever run, never removed, was the old behaviour —
    and the per-user 'already captioning?' check scans this dict on every
    request, so it got slower as it grew."""
    from src.dashboard import api
    import time as _t
    api._caption_jobs.clear()
    api._caption_jobs["old"]     = {"status": "done", "finished_at": _t.time() - 99999}
    api._caption_jobs["recent"]  = {"status": "failed", "finished_at": _t.time()}
    api._caption_jobs["live"]    = {"status": "running"}
    api._prune_caption_jobs()
    assert "old" not in api._caption_jobs, "finished jobs never expire"
    assert "recent" in api._caption_jobs, "a just-failed job must survive long "\
        "enough for a reconnecting tab to read the reason"
    assert "live" in api._caption_jobs, "pruned a RUNNING job"
    api._caption_jobs.clear()

    # ...and it has to actually run. Pruning nothing calls is the same as no
    # pruning, and the direct test above passes either way.
    import inspect
    assert "_prune_caption_jobs()" in inspect.getsource(api.start_captions), \
        "prune defined but never called — the dict still grows forever"


def test_the_vad_ab_tool_does_not_claim_cross_process_serialisation():
    """It runs as its own process, so cap._slot there is a different semaphore
    from the service's. It said otherwise, which would have had someone run it
    mid-stream believing it was safe."""
    import inspect
    from src.maintenance import caption_vad_test
    doc = caption_vad_test.__doc__ or ""
    assert "SEPARATE PROCESS" in doc and "different semaphore" in doc
    # The old wording is quoted in the docstring as the thing being corrected,
    # so "is the phrase absent" is not the test — "is it marked as wrong" is.
    assert "That was wrong" in doc
    # And the code comment at the acquire must not re-assert cross-process
    # safety either; that is where someone would read it and believe it.
    src = inspect.getsource(caption_vad_test._run)
    assert "does NOT serialise against the running" in src
