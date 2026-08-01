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


def test_the_model_is_configured_for_a_single_core_box():
    """cpu_threads must be pinned. Left to itself CTranslate2 takes every core
    it can see — on this box that is the only one, and the audio meters lose."""
    import inspect
    src = inspect.getsource(cap._load_model)
    assert "cpu_threads=1" in src, "Whisper would grab the only core"
    assert 'compute_type="int8"' in src, "float32 on a 2 GB box is asking for OOM"
