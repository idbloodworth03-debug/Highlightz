"""The read-only check behind "my downloaded clips have no sound"
(src/maintenance/export_audio_check.py).

It is run on production to tell the iPhone export bug (WebKit's AAC config
written verbatim, see test_export_audio_config.py) apart from every other way
a video ends up silent: no sound track at all, a track of silence, or a
server render or captured clip that is broken in some other way. A check that
mislabels one of those sends the next fix after the wrong thing, so each
verdict is pinned here on a real file of that kind, and so is the promise
that it writes nothing.
"""

import json
import shutil
import subprocess
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.maintenance import export_audio_check as eac
from src.uploads import library as lib

NODE = shutil.which("node") or "/opt/node22/bin/node"
FFMPEG = shutil.which("ffmpeg")
MUXER = Path("src/dashboard/static/vendor/mp4-muxer.js").resolve()
needs_tools = pytest.mark.skipif(not (Path(NODE).exists() and FFMPEG),
                                 reason="node and ffmpeg are needed to make the files")

ASC = [0x11, 0x90]


def _es(desc: list[int]) -> list[int]:
    """An ES_Descriptor around `desc`, with CoreAudio's 4-byte sizes."""
    dsi = [0x05, 0x80, 0x80, 0x80, len(desc), *desc]
    dcd_body = [0x40, 0x15, 0, 0x18, 0, 0, 0x02, 0x71, 0, 0, 0x02, 0x71, 0, *dsi]
    dcd = [0x04, 0x80, 0x80, 0x80, len(dcd_body), *dcd_body]
    body = [0, 0, 0, *dcd, 0x06, 0x80, 0x80, 0x80, 1, 2]
    return [0x03, 0x80, 0x80, 0x80, len(body), *body]


COOKIE = _es(ASC)                     # what WebKit's AudioEncoder hands over


# ── the config, read the way the file holds it ─────────────────────────────

def test_the_config_is_read_out_of_the_esds_whatever_the_browser_put_there():
    # The esds payload as mp4-muxer writes it: its own ES_Descriptor, with the
    # encoder's description as the DecoderSpecificInfo.
    assert eac.specific_info(bytes(_es(ASC))) == bytes(ASC)
    assert eac.specific_info(bytes(_es(COOKIE))) == bytes(COOKIE)
    # ffmpeg-style one-byte sizes.
    assert eac.specific_info(bytes([0x03, 0x19, 0, 1, 0, 0x04, 0x11, 0x40, 0x15,
                                    *[0] * 11, 0x05, 0x02, *ASC, 0x06, 0x01, 0x02])) == bytes(ASC)
    assert eac.specific_info(b"") is None
    assert eac.specific_info(bytes([0x03, 0x80, 0x80, 0x80, 0x80])) is None


def test_each_config_gets_the_right_name():
    assert eac.describe_config(bytes(ASC)) == ("ok", "AAC-LC 48000 Hz 2ch")
    assert eac.describe_config(bytes([0x12, 0x08])) == ("ok", "AAC-LC 44100 Hz 1ch")
    v, detail = eac.describe_config(bytes(COOKIE))
    assert v == "BROKEN (WebKit)" and detail.startswith("config is an ES_Descriptor: 03 80 80 80")
    # Rate index 15: the rate itself follows in 24 bits (22050 here).
    explicit = (2 << 35) | (15 << 31) | (22050 << 7) | (2 << 3)
    assert eac.describe_config(explicit.to_bytes(5, "big")) == ("ok", "AAC-LC 22050 Hz 2ch")
    assert eac.describe_config(bytes([0x16, 0x90]))[0] == "BROKEN (config)"   # index 13 is reserved
    assert eac.describe_config(None)[0] == "BROKEN (config)"
    assert eac.describe_config(bytes([0x11]))[0] == "BROKEN (config)"


# ── the whole report, on real files ────────────────────────────────────────

def _ff(*args):
    subprocess.run([FFMPEG, "-v", "error", "-y", *args], check=True)


def _browser_mp4(out: Path, aac: Path, description: list[int]) -> None:
    """An audio-only MP4 the way the Clip Editor muxes one, with `description`
    as the encoder's decoderConfig.description."""
    code = textwrap.dedent("""
    const fs = require('fs'), vm = require('vm');
    vm.runInThisContext(fs.readFileSync(%s, 'utf8'));
    const adts = fs.readFileSync(%s), frames = [];
    for (let i = 0; i + 7 <= adts.length;) {
      const hdr = (adts[i + 1] & 1) ? 7 : 9;
      const len = ((adts[i + 3] & 3) << 11) | (adts[i + 4] << 3) | (adts[i + 5] >> 5);
      frames.push(new Uint8Array(adts.subarray(i + hdr, i + len))); i += len;
    }
    const m = new Mp4Muxer.Muxer({ target: new Mp4Muxer.ArrayBufferTarget(),
      audio: { codec: 'aac', sampleRate: 48000, numberOfChannels: 2 }, fastStart: 'in-memory' });
    const dur = Math.round(1024 / 48000 * 1e6);
    frames.forEach((f, k) => m.addAudioChunkRaw(f, 'key', k * dur, dur, k ? undefined :
      { decoderConfig: { codec: 'mp4a.40.2', sampleRate: 48000, numberOfChannels: 2,
                         description: new Uint8Array(%s).buffer } }));
    m.finalize();
    fs.writeFileSync(%s, Buffer.from(m.target.buffer));
    """) % (json.dumps(str(MUXER)), json.dumps(str(aac)), json.dumps(description), json.dumps(str(out)))
    p = subprocess.run([NODE, "-e", code], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr


@pytest.fixture
def account(tmp_path, monkeypatch):
    """luclips' library and download log, one file of every kind."""
    from src.clips import files as clip_files
    from src.publish import schedule
    from src.stats import downloads

    src = tmp_path / "src"
    src.mkdir()
    tone = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2"]
    picture = ["-f", "lavfi", "-i", "testsrc=size=160x90:rate=10:duration=2"]
    _ff(*tone, "-ac", "2", "-c:a", "aac", "-f", "adts", str(src / "tone.aac"))
    _ff(*picture, *tone, "-c:v", "mpeg4", "-c:a", "aac", "-ac", "2", "-shortest", str(src / "server.mp4"))
    _ff(*picture, "-c:v", "mpeg4", "-an", str(src / "no_audio.mp4"))
    _ff(*picture, "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-c:v", "mpeg4",
        "-c:a", "aac", "-shortest", str(src / "silent.mp4"))
    _browser_mp4(src / "iphone.mp4", src / "tone.aac", COOKIE)
    _browser_mp4(src / "chrome.mp4", src / "tone.aac", ASC)

    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    lib.load()
    now = time.time()
    names = ["iphone", "chrome", "server", "no_audio", "silent"]
    for age, name in enumerate(names):
        up = lib.Upload(id=f"up-{name}", user_id="u1", filename=f"{name}.mp4", kind="mp4",
                        size=(src / f"{name}.mp4").stat().st_size, created_at=now - 3600 * (age + 1),
                        source="render")
        lib._uploads[up.id] = up
        lib.path_for(up).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src / f"{name}.mp4", lib.path_for(up))
    # Not an edited video, and too old: neither is listed.
    lib._uploads["up-own"] = lib.Upload(id="up-own", user_id="u1", filename="own.mp4", kind="mp4",
                                        size=1, created_at=now, source="upload")
    lib._uploads["up-old"] = lib.Upload(id="up-old", user_id="u1", filename="old.mp4", kind="mp4",
                                        size=1, created_at=now - 40 * 86400, source="render")

    monkeypatch.setattr(clip_files, "_ROOT", tmp_path / "clipfiles")
    (tmp_path / "clipfiles").mkdir()
    shutil.copy(src / "server.mp4", tmp_path / "clipfiles" / "clip-kept.mp4")
    log = tmp_path / "downloads.jsonl"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"ts": now - 600, "user_id": "u1", "clip_id": "clip-kept", "channel": "westcol", "platform": "kick"},
        {"ts": now - 500, "user_id": "u1", "clip_id": "clip-gone", "channel": "lacobraaa", "platform": "kick"},
        {"ts": now - 400, "user_id": "u2", "clip_id": "clip-other", "channel": "x", "platform": "twitch"},
    ]) + "\n{torn\n")
    monkeypatch.setattr(downloads, "_FILE", log)
    monkeypatch.setattr(eac, "_user", lambda who: {"id": "u1", "username": "luclips"} if who == "luclips" else None)
    due = now + 86400
    monkeypatch.setattr(schedule, "for_user", lambda uid: [
        SimpleNamespace(upload_id="up-iphone", status="pending", due_at=due)])
    yield tmp_path
    lib._uploads.clear()


def _report(**kw):
    lines = []
    rc = eac.report("luclips", out=lines.append, **kw)
    return rc, "\n".join(lines)


def _row(text, needle):
    return next(line for line in text.splitlines() if needle in line)


@needs_tools
def test_each_kind_of_file_gets_its_own_verdict(account):
    rc, text = _report(days=14)
    assert rc == 0
    library = text.split("== clip files")[0]
    rows = [line for line in library.splitlines() if line.startswith("   ") and "made by" not in line]
    assert len(rows) == 5, rows                    # own uploads and old renders are not listed

    iphone = rows[0]                               # newest first
    assert "browser export" in iphone and "BROKEN (WebKit)" in iphone and "03 80 80 80" in iphone
    assert "[Scheduler: pending, due" in iphone    # a silent video queued to post
    chrome = rows[1]
    assert "browser export" in chrome and " ok " in chrome and "AAC-LC 48000 Hz 2ch" in chrome
    server = rows[2]
    assert "server render" in server and " ok " in server and "mean" in server
    assert "no audio track" in rows[3]
    assert "silent" in rows[4] and "peak -91 dB" in rows[4]

    clips = text.split("== clip files")[1].split("== summary")[0]
    assert "westcol" in _row(clips, "westcol") and " ok " in _row(clips, "westcol")
    assert "file gone" in _row(clips, "lacobraaa")
    assert "clip-other" not in clips and "twitch" not in clips   # another account's

    summary = text.split("== summary ==")[1]
    assert "browser export  BROKEN (WebKit) 1, ok 1" in summary
    assert "server render   no audio track 1, ok 1, silent 1" in summary
    assert "clip file       ok 1" in summary


@needs_tools
def test_it_writes_nothing(account):
    def snapshot():
        return {str(p): (p.stat().st_size, p.stat().st_mtime_ns)
                for p in account.rglob("*") if p.is_file()}
    before = snapshot()
    _report(days=14)
    assert snapshot() == before


def test_an_unknown_account_is_said_so(monkeypatch):
    monkeypatch.setattr(eac, "_user", lambda who: None)
    lines = []
    assert eac.report("nobody", out=lines.append) == 2
    assert "No account 'nobody'" in lines[0]
