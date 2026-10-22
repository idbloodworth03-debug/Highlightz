"""Exports made on an iPhone had no sound (luclips, 2026-10-22).

WHAT HAPPENED. "The clips have audio when I preview them inside Highlightz.
After downloading them, the video files have no sound." The preview plays the
source file; the download was the Clip Editor's export, which on an iPhone is
the frame-accurate WebCodecs path: an AudioEncoder makes AAC, and mp4-muxer
writes the encoder's decoder `description` into the MP4 as the track's
AudioSpecificConfig. WebKit's AudioEncoder (Safari, and every iPhone browser,
Chrome included) hands back the whole MPEG-4 ES_Descriptor around those two
bytes instead (WebKit bug 302253, fixed upstream 2026-10-22, in no shipping
iOS). Its first byte, the descriptor tag 03, reads as audio object type 0, and
nothing can open the track. Nothing to do with Kick: luclips only clips Kick.

WHAT IS PINNED HERE, on the real code cut out of aurora_html.py:
  * an AudioSpecificConfig passes through untouched (Chrome, unchanged);
  * WebKit's descriptor, in each shape the spec allows, is unwrapped to it;
  * anything unreadable is dropped, so the muxer's own config stands;
  * end to end, with real AAC frames and the vendored mp4-muxer: WebKit's
    description makes a file ffmpeg cannot decode, the unwrapped one makes
    the same file Chrome's does, and Chrome's is byte-identical either way.
The device half (that iOS 26.6 hands over that descriptor) is WebKit's bug and
its fix, not something this container can run.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SRC = Path("src/dashboard/aurora_html.py").read_text()
JS = SRC.split('<script type="text/babel">')[1]
MUXER = Path("src/dashboard/static/vendor/mp4-muxer.js")
NODE = shutil.which("node") or "/opt/node22/bin/node"
FFMPEG = shutil.which("ffmpeg")

needs_node = pytest.mark.skipif(not Path(NODE).exists(), reason="node is not installed here")
needs_ffmpeg = pytest.mark.skipif(not (FFMPEG and shutil.which("ffprobe")),
                                  reason="ffmpeg is not installed here")

ASC = [0x11, 0x90]                    # AAC-LC, 48 kHz, stereo


def _helpers() -> str:
    a = JS.index("function aacAudioSpecificConfig(")
    b = JS.index("function _reportExportAudio(")
    return JS[a:b]


def _node(code: str):
    out = subprocess.run([NODE, "-e", textwrap.dedent(code)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _size(n: int, wide: bool) -> list[int]:
    """An ISO 14496-1 expandable size: CoreAudio pads to 4 bytes, others don't."""
    return [0x80, 0x80, 0x80, n] if wide else [n]


def _es_descriptor(asc, wide=True, flags=0x00, extra=()) -> list[int]:
    """ES_Descriptor > DecoderConfigDescriptor > DecoderSpecificInfo (+ SLConfig),
    the shape of the CoreAudio magic cookie WebKit hands over as `description`."""
    dsi = [0x05, *_size(len(asc), wide), *asc]
    dcd_body = [0x40, 0x15, 0x00, 0x18, 0x00, 0x00, 0x02, 0x71, 0x00, 0x00, 0x02, 0x71, 0x00, *dsi]
    dcd = [0x04, *_size(len(dcd_body), wide), *dcd_body]
    sl = [0x06, *_size(1, wide), 0x02]
    body = [0x00, 0x00, flags, *extra, *dcd, *sl]
    return [0x03, *_size(len(body), wide), *body]


def _configs(cases: dict) -> dict:
    return _node("""
    %s
    const out = {};
    for (const [name, bytes] of Object.entries(%s)) {
      const r = aacAudioSpecificConfig(new Uint8Array(bytes).buffer);
      out[name] = r ? Array.from(r) : null;
    }
    console.log(JSON.stringify(out));
    """ % (_helpers(), json.dumps(cases)))


@needs_node
def test_a_real_audio_specific_config_passes_through_untouched():
    got = _configs({"stereo48": ASC, "mono48": [0x11, 0x88], "stereo44": [0x12, 0x10],
                    "with_extension": [0x13, 0x10, 0x56, 0xE5, 0x98]})
    assert got == {"stereo48": ASC, "mono48": [0x11, 0x88], "stereo44": [0x12, 0x10],
                   "with_extension": [0x13, 0x10, 0x56, 0xE5, 0x98]}


@needs_node
def test_webkits_descriptor_is_unwrapped_in_every_shape_the_spec_allows():
    got = _configs({
        "coreaudio_cookie": _es_descriptor(ASC),
        "short_sizes": _es_descriptor(ASC, wide=False),
        "mono": _es_descriptor([0x11, 0x88]),
        # streamDependenceFlag, URL_Flag (length 3 + "abc") and OCRstreamFlag
        # each add a field before the DecoderConfigDescriptor.
        "all_flags": _es_descriptor(ASC, flags=0xE0, extra=(0x00, 0x07, 3, 97, 98, 99, 0x00, 0x09)),
        "decoder_config_only": _es_descriptor(ASC)[8:],
        "specific_info_only": [0x05, 0x02, *ASC],
    })
    assert got == {"coreaudio_cookie": ASC, "short_sizes": ASC, "mono": [0x11, 0x88],
                   "all_flags": ASC, "decoder_config_only": ASC, "specific_info_only": ASC}


@needs_node
def test_what_cannot_be_read_is_refused_rather_than_guessed():
    cookie = _es_descriptor(ASC)
    got = _configs({
        "empty": [], "one_byte": [0x11],
        "truncated": cookie[:28],                    # stops inside the config
        "inner_not_a_config": _es_descriptor([0x03, 0x00]),
        "unknown_tag": [0x07, 0x02, *ASC],
        "runaway_size": [0x05, 0x80, 0x80, 0x80, 0x80, 0x02, *ASC],
        "bad_rate_index": [0x16, 0x90],              # rate index 13 is reserved
    })
    assert all(v is None for v in got.values()), got


@needs_node
def test_the_chunk_metadata_keeps_or_fixes_or_drops_the_description():
    """Dropped means the KEY goes: mp4-muxer Object.assign()s the chunk's
    decoderConfig over its own, so `description: undefined` would replace its
    good config with nothing, and an absent key leaves that config alone."""
    got = _node("""
    %s
    const dc = (bytes) => ({ codec: 'mp4a.40.2', sampleRate: 48000, numberOfChannels: 2,
                             description: new Uint8Array(bytes).buffer });
    const show = m => m && m.decoderConfig
      ? { keys: Object.keys(m.decoderConfig).sort(),
          desc: 'description' in m.decoderConfig ? Array.from(new Uint8Array(m.decoderConfig.description)) : 'absent',
          other: m.other }
      : m === undefined ? 'undefined' : m;
    console.log(JSON.stringify({
      chrome: show(aacExportMeta({ decoderConfig: dc(%s), other: 1 })),
      webkit: show(aacExportMeta({ decoderConfig: dc(%s), other: 1 })),
      junk: show(aacExportMeta({ decoderConfig: dc([0x07, 0x01, 0x02]), other: 1 })),
      later_chunk: show(aacExportMeta(undefined)),
      no_description: show(aacExportMeta({ decoderConfig: { codec: 'mp4a.40.2' } })),
    }));
    """ % (_helpers(), json.dumps(ASC), json.dumps(_es_descriptor(ASC))))
    keys = ["codec", "description", "numberOfChannels", "sampleRate"]
    assert got["chrome"] == {"keys": keys, "desc": ASC, "other": 1}
    assert got["webkit"] == {"keys": keys, "desc": ASC, "other": 1}
    assert got["junk"] == {"keys": ["codec", "numberOfChannels", "sampleRate"], "desc": "absent", "other": 1}
    assert got["later_chunk"] == "undefined"
    assert got["no_description"] == {"keys": ["codec"], "desc": "absent"}     # `other` was never set


def test_the_export_hands_the_muxer_fixed_metadata_and_reports_lost_audio():
    fa = JS[JS.index("async function exportFrameAccurate("):JS.index("/* ── Export audio")]
    assert "muxer.addAudioChunk(chunk, sup.mux === 'mp4' ? aacExportMeta(meta) : meta)" in fa
    assert re.search(r"_encodeAudioOffline\([^)]*\)\.catch\(e => \{ _reportExportAudio\('encode', e\)", fa)
    enc = JS[JS.index("async function _encodeAudioOffline("):JS.index("async function exportFrameAccurate(")]
    assert "_reportExportAudio('decode', e)" in enc
    # The report goes through the page's reporter, which the server logs as
    # client_error; a kind over 20 characters would be cut there.
    rep = JS[JS.index("function _reportExportAudio("):JS.index("// The clip's own sound")]
    assert "window.hzReport('export_audio'" in rep and len("export_audio") <= 20


# ── end to end: real AAC frames through the vendored muxer ─────────────────

_MUX = """
const fs = require('fs'), vm = require('vm');
Date.now = () => 0;                        // the muxer stamps the wall clock into mvhd/tkhd/mdhd
vm.runInThisContext(fs.readFileSync(%(muxer)s, 'utf8'));
vm.runInThisContext(%(helpers)s);
const adts = fs.readFileSync(%(aac)s), frames = [];
for (let i = 0; i + 7 <= adts.length;) {
  const hdr = (adts[i + 1] & 1) ? 7 : 9;
  const len = ((adts[i + 3] & 3) << 11) | (adts[i + 4] << 3) | (adts[i + 5] >> 5);
  frames.push(new Uint8Array(adts.subarray(i + hdr, i + len)));
  i += len;
}
function mux(out, description, fix) {
  const m = new Mp4Muxer.Muxer({ target: new Mp4Muxer.ArrayBufferTarget(),
    audio: { codec: 'aac', sampleRate: 48000, numberOfChannels: 2 },
    fastStart: 'in-memory', firstTimestampBehavior: 'offset' });
  const dur = Math.round(1024 / 48000 * 1e6);
  frames.forEach((f, k) => {
    let meta = k ? undefined : { decoderConfig: { codec: 'mp4a.40.2', sampleRate: 48000, numberOfChannels: 2,
                                                  description: new Uint8Array(description).buffer } };
    if (fix) meta = aacExportMeta(meta);
    m.addAudioChunkRaw(f, 'key', k * dur, dur, meta);
  });
  m.finalize();
  fs.writeFileSync(out, Buffer.from(m.target.buffer));
}
const dir = %(dir)s;
mux(dir + '/chrome.mp4', %(asc)s, false);
mux(dir + '/chrome_fixed.mp4', %(asc)s, true);
mux(dir + '/webkit.mp4', %(cookie)s, false);
mux(dir + '/webkit_fixed.mp4', %(cookie)s, true);
console.log(JSON.stringify({ frames: frames.length }));
"""


def _decode(path: Path) -> tuple[int, str]:
    out = subprocess.run([FFMPEG, "-hide_banner", "-i", str(path), "-map", "0:a",
                          "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True)
    return out.returncode, out.stderr


@needs_node
@needs_ffmpeg
def test_webkits_description_makes_a_silent_file_and_the_fix_makes_chromes(tmp_path):
    aac = tmp_path / "tone.aac"
    subprocess.run([FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:sample_rate=48000:duration=2", "-ac", "2",
                    "-c:a", "aac", "-b:a", "160k", "-f", "adts", str(aac)], check=True)
    got = _node(_MUX % {"muxer": json.dumps(str(MUXER)), "helpers": json.dumps(_helpers()),
                        "aac": json.dumps(str(aac)), "dir": json.dumps(str(tmp_path)),
                        "asc": json.dumps(ASC), "cookie": json.dumps(_es_descriptor(ASC))})
    assert got["frames"] > 50

    # The bug, as a file: the track is there and nothing can open it.
    rc, err = _decode(tmp_path / "webkit.mp4")
    assert rc != 0 and "Audio object type 0" in err, err[-400:]

    # The fix: a track that decodes, with the tone in it (silence is -91 dB).
    for name in ("chrome.mp4", "webkit_fixed.mp4"):
        rc, err = _decode(tmp_path / name)
        assert rc == 0, err[-400:]
        mean = float(re.search(r"mean_volume: (-?[\d.]+) dB", err).group(1))
        assert mean > -40, (name, mean)
        probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                                "stream=codec_name,profile,sample_rate,channels", "-of", "json",
                                str(tmp_path / name)], capture_output=True, text=True, check=True)
        s = json.loads(probe.stdout)["streams"][0]
        assert (s["codec_name"], s["profile"], s["sample_rate"], s["channels"]) == ("aac", "LC", "48000", 2)

    # WebKit's file, fixed, is Chrome's file; and Chrome's own is untouched.
    chrome = (tmp_path / "chrome.mp4").read_bytes()
    assert (tmp_path / "webkit_fixed.mp4").read_bytes() == chrome
    assert (tmp_path / "chrome_fixed.mp4").read_bytes() == chrome
