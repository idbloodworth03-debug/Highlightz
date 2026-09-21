"""The Ollama builder: an EditPlan decided by a model you host yourself.

Owner (2026-09-21): "how could I use ollama on my site I want to use that llm
since it is free and good."

WHAT IS FREE AND WHAT IS NOT. The tokens are free — there is no bill per
clip, which is the whole point. The MACHINE is not. Ollama needs somewhere to
run, and that somewhere is emphatically NOT the droplet: prod measured
2 vCPU, 3.8 GiB and NO SWAP on 2026-09-21, with 3.0 GiB available at idle and
less than that the moment channels go live. A 3B model at 4-bit is ~2 GB of
weights before its KV cache; with no swap, an allocation past available RAM
does not get slow, it gets the OOM killer, and the OOM killer picks the
biggest process — the server running the whole product. The LIVE ceiling is
derived from the core count too (cores x 6 = 12), so inference on those cores
makes that ceiling a lie.

So this module talks to Ollama over HTTP and does not care where it lives.
`OLLAMA_BASE_URL` points at your desktop, a GPU box, or anything else
reachable from the droplet. Pointing it at the droplet's own localhost is the
one configuration that can take the site down, so `warn_if_local()` says so
out loud and `scripts/ollama_check.py` does the RAM arithmetic for you.

NO NEW DEPENDENCY. This uses httpx, which is already in requirements.txt and
already installed on prod — unlike the Anthropic path, there is nothing to
`pip install` before it works.

WHAT IS DIFFERENT ABOUT A SMALL MODEL, and why the clamps in llm_common
matter more here than they do for Claude: a 3-8B model follows a JSON schema
well (Ollama constrains the decoder with it, so the SHAPE is reliable) but
follows SEMANTIC constraints poorly. It will return valid JSON describing a
segment that runs past the end of the file, or four segments totalling three
minutes, far more often than a large model will. Every one of those is
clamped rather than rejected, and every clamp is recorded in `notes` so you
can see in the logs how much correcting your model needs. If the notes are
long on every clip, that model is not good enough for this job.
"""

from __future__ import annotations

import json
import time
from urllib.parse import urlparse

import httpx
import structlog

from config.settings import settings
from src.autopilot import plan as P
from src.autopilot.llm_common import SCHEMA, SYSTEM, brief, coerce, copy_for, formula

log = structlog.get_logger(__name__)

# Hostnames that mean "the machine the server is running on". Pointing Ollama
# at one of these from prod is the configuration that ends in an OOM kill.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}

# Deterministic-ish. This is a structural task with one right-ish answer, not
# a creative writing exercise, and a hot small model invents events that are
# not in the transcript. Ollama's own default is 0.8.
TEMPERATURE = 0.4
# Ollama silently TRUNCATES the prompt to the context window and answers
# anyway, so a brief with transcripts can lose its tail without any error at
# all. 8192 fits the largest brief this sends (~4.2k tokens) with room for
# the answer. A model whose context is smaller will still truncate — check
# `scripts/ollama_check.py`, which prints what the model reports.
NUM_CTX = 8192
# How long Ollama keeps the weights resident after a call. Loading a model
# from disk is most of the latency on a cold call, and clips arrive in bursts
# when a stream is live, so holding it is worth the RAM on a dedicated box.
KEEP_ALIVE = "10m"


def base_url() -> str:
    return (settings.ollama_base_url or "").rstrip("/")


def configured() -> bool:
    """Whether an Ollama plan can be attempted. No key, no package — just a
    URL and a model name, which is the nice thing about this path."""
    return bool(base_url() and settings.ollama_model)


def is_local(url: str = "") -> bool:
    """Whether this URL points at the machine the server itself runs on."""
    try:
        return (urlparse(url or base_url()).hostname or "") in _LOCAL_HOSTS
    except ValueError:
        return False


def warn_if_local() -> str:
    """One warning, returned as well as logged so the check script and the
    admin view can both show it.

    Not an error and not a refusal: a laptop running both the dev server and
    Ollama is a perfectly good setup, and it is only prod where this is
    dangerous. The operator is told which one they have.
    """
    if not is_local():
        return ""
    msg = ("OLLAMA_BASE_URL points at this machine. On the production droplet "
           "(2 vCPU / 3.8 GiB / no swap) a model large enough to be useful "
           "will not fit beside the server, the streams and Whisper, and with "
           "no swap the OOM killer takes the biggest process — which is the "
           "server. Run Ollama somewhere else and point this at it.")
    log.warning("ollama_base_url_is_local", detail=msg)
    return msg


async def tags() -> list[dict]:
    """The models installed on that Ollama, or [] if it cannot be reached.

    Used by the check script and by `build` for a better error message than
    "connection refused" when the model name is simply wrong.
    """
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base_url()}/api/tags")
            r.raise_for_status()
            return (r.json() or {}).get("models") or []
    except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
        log.warning("ollama_tags_failed", error=str(exc)[:200])
        return []


async def build(clips: list[dict], sources: dict, *,
                transcripts: dict | None = None,
                target_s: float = P.TARGET_S, mode: str = "clipper",
                facecams: dict | None = None) -> tuple[P.EditPlan, dict]:
    """An EditPlan from your own model, or the formula's if anything goes wrong.

    The contract is identical to llm_plan.build — same arguments, same
    (plan, meta) return — because builder.py picks between them and nothing
    downstream should be able to tell which one ran.
    """
    if not configured():
        return formula(clips, sources, "Ollama not configured", target_s=target_s, mode=mode,
                       facecams=facecams)
    if not any(c.get("id") in sources for c in clips):
        return formula(clips, sources, "no clip has a file", target_s=target_s, mode=mode,
                       facecams=facecams)

    payload = brief(clips, sources, transcripts, facecams)
    body = {
        "model": settings.ollama_model,
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user",
                      "content": json.dumps(payload, separators=(",", ":"))}],
        # Ollama constrains the decoder with this schema, so the shape comes
        # back right even from a small model. The VALUES still need coerce().
        "format": SCHEMA,
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": TEMPERATURE, "num_ctx": NUM_CTX},
    }

    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=settings.ollama_timeout_s) as client:
            r = await client.post(f"{base_url()}/api/chat", json=body)
            r.raise_for_status()
            envelope = r.json()
    except httpx.TimeoutException:
        log.warning("ollama_timeout", seconds=settings.ollama_timeout_s,
                    model=settings.ollama_model)
        return formula(clips, sources,
                       f"Ollama did not answer in {settings.ollama_timeout_s:.0f}s",
                       target_s=target_s, mode=mode,
                       facecams=facecams)
    except httpx.HTTPStatusError as exc:
        # A 404 here almost always means the model is not pulled, which is a
        # much more useful thing to say than the status code.
        detail = f"HTTP {exc.response.status_code}"
        if exc.response.status_code == 404:
            installed = [m.get("name") for m in await tags()]
            detail = (f"model {settings.ollama_model!r} not found on that Ollama"
                      + (f" (installed: {', '.join(filter(None, installed))})"
                         if installed else ""))
        log.warning("ollama_http_error", error=detail)
        return formula(clips, sources, f"Ollama error: {detail}", target_s=target_s, mode=mode,
                       facecams=facecams)
    except httpx.HTTPError as exc:
        log.warning("ollama_unreachable", error=str(exc)[:200], url=base_url())
        return formula(clips, sources,
                       f"Ollama unreachable at {base_url()}: {str(exc)[:80]}",
                       target_s=target_s, mode=mode,
                       facecams=facecams)
    except (json.JSONDecodeError, ValueError) as exc:
        return formula(clips, sources, f"Ollama sent no JSON: {str(exc)[:80]}",
                       target_s=target_s, mode=mode,
                       facecams=facecams)

    try:
        text = (envelope.get("message") or {}).get("content") or ""
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("top level is not an object")
    except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
        # The schema should make this impossible, but an older Ollama ignores
        # a schema it does not understand and free-runs instead.
        log.warning("ollama_unreadable", error=str(exc)[:200])
        return formula(clips, sources, "Ollama returned nothing readable",
                       target_s=target_s, mode=mode,
                       facecams=facecams)

    plan, notes = coerce(data, sources, clips, target_s=target_s,
                         mode=mode, facecams=facecams)
    ok, why = P.valid(plan)
    if not ok:
        log.warning("ollama_plan_invalid", reason=why, notes=notes)
        return formula(clips, sources, f"plan invalid: {why}", target_s=target_s, mode=mode,
                       facecams=facecams)

    took = round(time.time() - t0, 2)
    log.info("llm_plan_built", provider="ollama", model=settings.ollama_model,
             segments=len(plan.segments), duration=round(P.plan_duration(plan), 2),
             notes=notes, took=took,
             prompt_eval_count=envelope.get("prompt_eval_count"),
             eval_count=envelope.get("eval_count"))
    return plan, {"source": "llm", "provider": "ollama", "reason": "",
                  "copy": copy_for(data, plan), "notes": notes,
                  "took": took, "model": settings.ollama_model}
