"""
Does an account's video have a sound track anything can play?

    venv/bin/python -m src.maintenance.export_audio_check <username or user id> [--days 14] [--max 25]

Read-only. luclips, 2026-10-22: "The clips have audio when I preview them
inside Highlightz. After downloading them, the video files have no sound."

The preview plays the clip's own file, cut on the server. A download is one
of three files, made in three different places, and this looks at each:

  browser export   the Clip Editor's render, encoded IN THE BROWSER and saved
                   back to the library (upload source "render", no ffmpeg tag)
  server render    Autopilot's edit, made by ffmpeg (same source, "Lavf" tag)
  clip file        the captured clip itself, from a Download button
                   (clips/downloads.jsonl), the same bytes the preview plays

For each file, the audio track's AAC config is read from the MP4's own boxes,
and the first 20 seconds are decoded at the lowest CPU priority. The verdicts:

  ok                AAC that decodes, with its loudness
  BROKEN (WebKit)   the config is an MPEG-4 ES_Descriptor, starting 03: what
                    WebKit's AudioEncoder hands over (WebKit bug 302253) and
                    the Clip Editor wrote verbatim until 2026-10-22. Nothing
                    can open it; the video plays silent everywhere.
  BROKEN (config)   some other unreadable config
  no audio track    the file has no sound track at all
  silent            a track that decodes to silence
  no decode         a readable config ffmpeg still could not decode

It writes nothing. ffmpeg decodes to the null muxer, under `nice`.
"""

import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from config.settings import settings

MB = 1024 * 1024
_CONTAINERS = {b"moov", b"trak", b"mdia", b"minf", b"stbl"}
_RATES = (96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350)


def _boxes(buf: bytes, start: int, end: int):
    """(type, payload start, end) for each box in buf[start:end]."""
    i = start
    while i + 8 <= end:
        size, kind, head = int.from_bytes(buf[i:i + 4], "big"), buf[i + 4:i + 8], 8
        if size == 1:
            size, head = int.from_bytes(buf[i + 8:i + 16], "big"), 16
        elif size == 0:
            size = end - i
        if size < head or i + size > end:
            return
        yield kind, i + head, i + size
        i += size


def _moov(path: Path) -> bytes | None:
    """The movie box, found by walking the top level; the media is never read."""
    total = path.stat().st_size
    with path.open("rb") as f:
        pos = 0
        while pos + 8 <= total:
            f.seek(pos)
            hdr = f.read(16)
            size, kind, head = int.from_bytes(hdr[:4], "big"), hdr[4:8], 8
            if size == 1:
                size, head = int.from_bytes(hdr[8:16], "big"), 16
            elif size == 0:
                size = total - pos
            if size < head:
                return None
            if kind == b"moov":
                if size > 64 * MB:
                    return None
                f.seek(pos)
                return f.read(size)
            pos += size
    return None


def _find(buf: bytes, start: int, end: int, path: list[bytes]):
    """The payload span of the first box at `path` under buf[start:end]."""
    for kind, s, e in _boxes(buf, start, end):
        if kind == path[0]:
            return (s, e) if len(path) == 1 else _find(buf, s, e, path[1:])
    return None


def _size(b: bytes, i: int) -> tuple[int, int]:
    """An ISO 14496-1 expandable size at b[i]: (value, next index), -1 if bad."""
    n = 0
    for _ in range(4):
        if i >= len(b):
            return -1, i
        v = b[i]
        i += 1
        n = n * 128 + (v & 127)
        if v < 128:
            return n, i
    return -1, i


def specific_info(esds: bytes) -> bytes | None:
    """The DecoderSpecificInfo bytes in an esds payload (after its version and
    flags): what the file says the AudioSpecificConfig is."""
    i = 0
    if i < len(esds) and esds[i] == 3:                     # ES_Descriptor
        _, i = _size(esds, i + 1)
        if i < 0 or i + 3 > len(esds):
            return None
        flags = esds[i + 2]
        i += 3
        i += 2 if flags & 128 else 0
        if flags & 64 and i < len(esds):
            i += 1 + esds[i]
        i += 2 if flags & 32 else 0
    if i < len(esds) and esds[i] == 4:                     # DecoderConfigDescriptor
        n, i = _size(esds, i + 1)
        if n < 0:
            return None
        i += 13
    if i >= len(esds) or esds[i] != 5:                     # DecoderSpecificInfo
        return None
    n, i = _size(esds, i + 1)
    if n < 0 or i + n > len(esds):
        return None
    return esds[i:i + n]


def audio_track(path: Path) -> dict:
    """{'track': bool, 'entry': 'mp4a'|..., 'config': bytes|None, 'lavf': bool}."""
    moov = _moov(path)
    if moov is None:
        return {"track": False, "error": "no moov box"}
    lavf = b"Lavf" in moov
    for kind, s, e in _boxes(moov, 8, len(moov)):
        if kind != b"trak":
            continue
        hdlr = _find(moov, s, e, [b"mdia", b"hdlr"])
        if not hdlr or moov[hdlr[0] + 8:hdlr[0] + 12] != b"soun":
            continue
        stsd = _find(moov, s, e, [b"mdia", b"minf", b"stbl", b"stsd"])
        if not stsd:
            return {"track": True, "entry": "?", "config": None, "lavf": lavf}
        for entry, es, ee in _boxes(moov, stsd[0] + 8, stsd[1]):
            if entry != b"mp4a":
                return {"track": True, "entry": entry.decode("latin-1"), "config": None, "lavf": lavf}
            version = int.from_bytes(moov[es + 8:es + 10], "big")
            kids = es + 28 + {1: 16, 2: 36}.get(version, 0)
            esds = _find(moov, kids, ee, [b"esds"])
            config = specific_info(moov[esds[0] + 4:esds[1]]) if esds else None
            return {"track": True, "entry": "mp4a", "config": config, "lavf": lavf}
    return {"track": False, "lavf": lavf}


def describe_config(c: bytes | None) -> tuple[str, str]:
    """(verdict, what the bytes say)."""
    if not c:
        return "BROKEN (config)", "no AudioSpecificConfig"
    hexed = " ".join(f"{x:02x}" for x in c[:8]) + (" ..." if len(c) > 8 else "")
    if c[0] == 3:
        return "BROKEN (WebKit)", f"config is an ES_Descriptor: {hexed}"
    if len(c) < 2 or not c[0] >> 3:
        return "BROKEN (config)", f"unreadable config: {hexed}"
    aot, rate = c[0] >> 3, ((c[0] & 7) << 1) | (c[1] >> 7)
    if rate <= 12:
        hz, chans = _RATES[rate], (c[1] >> 3) & 15
    elif rate == 15 and len(c) >= 5:                         # the rate follows in 24 bits
        bits = int.from_bytes(c[:5], "big")
        hz, chans = (bits >> 7) & 0xFFFFFF, (bits >> 3) & 15
    else:
        return "BROKEN (config)", f"unreadable config: {hexed}"
    name = {2: "AAC-LC", 5: "HE-AAC", 29: "HE-AACv2"}.get(aot, f"AAC type {aot}")
    return "ok", f"{name} {hz} Hz {chans}ch"


def decode(path: Path, seconds: int = 20) -> dict:
    """Decode the first `seconds` of the audio to nothing, at low priority."""
    cmd = [settings.ffmpeg_path or "ffmpeg", "-nostdin", "-hide_banner",
           "-t", str(seconds), "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    if shutil.which("nice"):
        cmd = ["nice", "-n", "19", *cmd]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc)[:120]}
    mean = re.search(r"mean_volume: (-?[\d.]+) dB", p.stderr)
    peak = re.search(r"max_volume: (-?[\d.]+) dB", p.stderr)
    errs = [ln.strip() for ln in p.stderr.splitlines()
            if re.search(r"error|not implemented|invalid|could not", ln, re.I)]
    return {"ok": p.returncode == 0 and bool(mean), "mean": float(mean.group(1)) if mean else None,
            "peak": float(peak.group(1)) if peak else None, "error": (errs[0] if errs else "")[:120]}


def verdict(path: Path, run_decoder: bool = True) -> tuple[str, str, bool]:
    """(verdict, detail, made by ffmpeg) for one file. A WebM is decoded only:
    its boxes are not MP4 boxes, and only the browser writes one."""
    if path.suffix.lower() == ".webm":
        t = {"track": True, "entry": "webm", "lavf": False}
    else:
        try:
            t = audio_track(path)
        except OSError as exc:
            return "unreadable", str(exc)[:120], False
    lavf = bool(t.get("lavf"))
    if not t.get("track"):
        return "no audio track", t.get("error", ""), lavf
    if t.get("entry") == "mp4a":
        v, detail = describe_config(t["config"])
        if v != "ok" or not run_decoder:
            return v, detail, lavf
    else:
        detail = f"{t.get('entry')} audio"
        if not run_decoder:
            return "not checked", detail, lavf
    d = decode(path)
    if not d["ok"]:
        return "no decode", f"{detail}; {d.get('error') or 'ffmpeg failed'}", lavf
    if d["peak"] is not None and d["peak"] <= -90:
        return "silent", detail + _loudness(d), lavf
    return "ok", detail + _loudness(d), lavf


def _loudness(d: dict) -> str:
    return f", mean {d['mean']:.0f} dB, peak {d['peak']:.0f} dB" if d.get("mean") is not None else ""


def _when(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:%M")


def _user(who: str) -> dict | None:
    from src.auth import users as user_store
    return user_store.get_by_id(who) or user_store.get_by_username(who)


def report(who: str, days: float = 14, limit: int = 25, out=print, run_decoder: bool = True) -> int:
    u = _user(who)
    if not u:
        out(f"No account {who!r} (give a username or a user id).")
        return 2
    uid, since = u["id"], time.time() - days * 86400
    counts: dict[str, dict[str, int]] = {}

    def tally(group: str, v: str) -> None:
        counts.setdefault(group, {})
        counts[group][v] = counts[group].get(v, 0) + 1

    from src.uploads import library as upload_lib
    from src.publish import schedule
    queued = {i.upload_id: i for i in schedule.for_user(uid)}
    renders = [up for up in upload_lib.for_user(uid)
               if up.source == "render" and up.created_at >= since][:limit]
    out(f"== {u.get('username') or uid}: edited videos in the library, last {days:g} days "
        f"(newest {len(renders)}) ==")
    out("   saved (UTC)   made by         size     verdict           detail")
    for up in renders:
        path = upload_lib.path_for(up)
        if not path.is_file():
            out(f"   {_when(up.created_at)}   -               -        file gone")
            continue
        v, detail, lavf = verdict(path, run_decoder)
        maker = "server render" if lavf else "browser export"
        tally(maker, v)
        item = queued.get(up.id)
        sched = ""
        if item:
            sched = (f"  [Scheduler: {item.status}"
                     + (f", due {_when(item.due_at)}" if item.due_at else ", no time set") + "]")
        out(f"   {_when(up.created_at)}   {maker:15} {up.size / MB:5.1f}MB  {v:17} {detail}{sched}")

    out(f"\n== clip files downloaded, last {days:g} days (the file the preview plays) ==")
    from src.clips import files as clip_files
    from src.stats import downloads as dl_log
    rows = []
    try:
        with dl_log._FILE.open(encoding="utf-8") as fh:      # read directly: _read() may prune
            for raw in fh:
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                if r.get("user_id") == uid and float(r.get("ts") or 0) >= since:
                    rows.append(r)
    except OSError:
        pass
    seen = set()
    for r in sorted(rows, key=lambda r: -float(r.get("ts") or 0)):
        cid = r.get("clip_id") or ""
        if cid in seen:
            continue
        seen.add(cid)
        if len(seen) > limit:
            break
        path = clip_files.path_for(cid)
        if not path or not path.is_file():
            v, detail = "file gone", "evicted since"
        else:
            v, detail, _ = verdict(path, run_decoder)
            tally("clip file", v)
        out(f"   {_when(float(r.get('ts') or 0))}   {(r.get('platform') or '?'):6} "
            f"{(r.get('channel') or '?')[:18]:18} {v:17} {detail}")
    if not seen:
        out("   none")

    out("\n== summary ==")
    for group in ("browser export", "server render", "clip file"):
        if group in counts:
            out(f"   {group:15} " + ", ".join(f"{k} {n}" for k, n in sorted(counts[group].items())))
    if not counts:
        out("   nothing to check in this window")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0].startswith("-"):
        print(__doc__.strip().splitlines()[0])
        print("\nusage: python -m src.maintenance.export_audio_check <username or user id> [--days 14] [--max 25]")
        return 2
    days = float(args[args.index("--days") + 1]) if "--days" in args else 14
    limit = int(args[args.index("--max") + 1]) if "--max" in args else 25
    return report(args[0], days, limit)


if __name__ == "__main__":
    raise SystemExit(main())
