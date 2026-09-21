"""Can this box talk to Ollama, and should it be the same box? Run on PROD.

    /opt/highlightz/venv/bin/python scripts/ollama_check.py

WHY THIS EXISTS. Two questions decide whether the Ollama builder is a good
idea, and neither can be answered from the dev container:

  1. Is the configured Ollama reachable, and is the model actually pulled?
     A wrong model name comes back from /api/chat as a bare 404.
  2. Would running it HERE fit? Prod is 2 vCPU / 3.8 GiB with no swap, and
     without swap an over-allocation is not a slowdown — it is the OOM
     killer choosing the biggest process, which is the server itself.

It reads memory from /proc, asks Ollama what it has, and does the subtraction
out loud. It changes nothing and posts nothing.
"""
import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings                          # noqa: E402
from src.autopilot import builder                             # noqa: E402
from src.autopilot import ollama_plan as O                    # noqa: E402

GB = 1024 ** 3


def meminfo() -> dict:
    """MemTotal / MemAvailable / SwapTotal in bytes, from /proc/meminfo.

    MemAvailable is the number that matters, not MemFree: page cache is
    reclaimable, so "free" understates what a new process can have.
    """
    out = {}
    try:
        for line in pathlib.Path("/proc/meminfo").read_text().splitlines():
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable", "SwapTotal"):
                out[key] = int(rest.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ask", action="store_true",
                    help="also send one tiny real prompt and time it")
    args = ap.parse_args()

    print("=" * 66)
    print("OLLAMA CHECK")
    print("=" * 66)

    st = builder.status()
    print(f"\nLLM_PROVIDER : {st['provider']}")
    print(f"target       : {st['detail']}")
    print(f"configured   : {st['configured']}")
    if st["provider"] != "ollama":
        print("\n  (LLM_PROVIDER is not 'ollama' — checking the endpoint anyway.)")

    # ── the machine ─────────────────────────────────────────────────────────
    mem = meminfo()
    total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    swap = mem.get("SwapTotal", 0)
    print("\nTHIS MACHINE")
    print(f"  RAM total    : {total / GB:.1f} GiB")
    print(f"  RAM available: {avail / GB:.1f} GiB")
    print(f"  swap         : {swap / GB:.1f} GiB"
          + ("   <- none: an over-allocation is an OOM kill, not a slowdown"
             if swap == 0 else ""))

    local = O.is_local()
    print(f"  Ollama is on : {'THIS machine' if local else 'another machine'}")
    if local:
        print("\n  " + "!" * 62)
        for line in ("OLLAMA_BASE_URL points here. Anything you run has to fit",
                     "BESIDE the server, streamlink and an ffmpeg audio meter per",
                     "live channel, and Whisper when a caption job starts. The",
                     "figure above is measured right now, not at peak."):
            print(f"  ! {line}")
        print("  " + "!" * 62)

    # ── the endpoint ────────────────────────────────────────────────────────
    print(f"\nENDPOINT  {O.base_url()}")
    models = await O.tags()
    if not models:
        print("  UNREACHABLE, or it has no models pulled.")
        print("  On the machine running Ollama:  ollama serve   /   ollama pull <model>")
        print("  If it is a different machine it must listen off-localhost:")
        print("      OLLAMA_HOST=0.0.0.0 ollama serve")
        print("  and be reachable from here — check any firewall between them.")
        return 1

    print(f"  reachable, {len(models)} model(s) installed:")
    want = settings.ollama_model
    found = None
    for m in models:
        name = m.get("name") or m.get("model") or "?"
        size = int(m.get("size") or 0)
        params = (m.get("details") or {}).get("parameter_size") or "?"
        mark = "  <- OLLAMA_MODEL" if name == want else ""
        print(f"    {name:<34} {size / GB:5.1f} GiB  {params:>6}{mark}")
        if name == want:
            found = m

    if found is None:
        print(f"\n  OLLAMA_MODEL is {want!r}, which is NOT in that list.")
        print(f"  /api/chat would 404. Pull it:  ollama pull {want}")
        return 1

    # ── the arithmetic ──────────────────────────────────────────────────────
    size = int(found.get("size") or 0)
    # Weights on disk are the floor. The KV cache, the runtime and the graph
    # add on top; a third is a rough but honest allowance at this context
    # size, and rough in the direction of caution.
    need = size * 1.33
    print(f"\nFIT (only meaningful if Ollama runs on THIS machine)")
    print(f"  weights          : {size / GB:.1f} GiB")
    print(f"  with KV + runtime: ~{need / GB:.1f} GiB")
    print(f"  available now    : {avail / GB:.1f} GiB")
    if not local:
        print("  -> not this machine's problem; check the RAM on the box that runs it.")
    elif need > avail:
        print(f"  -> DOES NOT FIT. Short by ~{(need - avail) / GB:.1f} GiB"
              + (" and there is no swap to absorb it." if swap == 0 else "."))
    elif need > avail * 0.6:
        print("  -> fits RIGHT NOW, but this reading is at the current load.")
        print("     Every live channel adds streamlink + an ffmpeg meter, and a")
        print("     caption job loads Whisper. This is the configuration that")
        print("     looks fine until a stream goes live.")
    else:
        print("  -> fits with room.")

    if args.ask:
        import time
        print("\nONE REAL CALL (tiny prompt, just to time the round trip)")
        import httpx
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=settings.ollama_timeout_s) as c:
                r = await c.post(f"{O.base_url()}/api/chat", json={
                    "model": want, "stream": False, "keep_alive": O.KEEP_ALIVE,
                    "messages": [{"role": "user", "content": "Reply with the word ok."}]})
                r.raise_for_status()
                env = r.json()
            took = time.time() - t0
            print(f"  answered in {took:.1f}s "
                  f"(load {int(env.get('load_duration') or 0) / 1e9:.1f}s, "
                  f"{env.get('eval_count')} tokens)")
            print(f"  said: {((env.get('message') or {}).get('content') or '')[:80]!r}")
            if took > settings.ollama_timeout_s / 3:
                print(f"  NOTE: a real brief is far bigger than this. "
                      f"OLLAMA_TIMEOUT_S is {settings.ollama_timeout_s:.0f}s.")
        except Exception as exc:
            print(f"  FAILED: {str(exc)[:200]}")
            return 1

    print("\nNext:  venv/bin/python scripts/edit_preview.py --llm")
    print("       (builds a real plan through the configured provider)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
