"""Which builder decides the edit. One entry point for everything else.

THE POINT OF THIS MODULE IS THAT NOTHING ELSE HAS TO KNOW. runner.py,
edit_preview.py and the admin view call `build()` and get an EditPlan. Which
provider produced it — Claude, your own Ollama, or the deterministic formula
in plan.py — is one line in .env:

    LLM_PROVIDER=none        the formula. The default, and always safe.
    LLM_PROVIDER=ollama      a model you host. Free per clip; you own the box.
    LLM_PROVIDER=anthropic   Claude. Costs per clip; nothing to host.

Every provider falls back to the formula on every failure, so the worst case
of a misconfigured provider is the video the product made yesterday. There is
no configuration of this module that loses a post.
"""

from __future__ import annotations

import structlog

from config.settings import settings
from src.autopilot import plan as P
from src.autopilot.llm_common import formula

log = structlog.get_logger(__name__)

PROVIDERS = ("none", "ollama", "anthropic")


def provider() -> str:
    """The configured provider, or "none" if it is unset or nonsense."""
    name = (settings.llm_provider or "none").strip().lower()
    return name if name in PROVIDERS else "none"


def status() -> dict:
    """What is switched on, for the check script and the admin view.

    Deliberately says nothing secret: whether a key exists, never the key.
    """
    from src.autopilot import llm_plan, ollama_plan
    name = provider()
    out = {"provider": name, "configured": False, "detail": "", "warning": ""}
    if name == "anthropic":
        out["configured"] = llm_plan.configured()
        out["detail"] = settings.llm_model
        if not out["configured"]:
            out["warning"] = ("set ANTHROPIC_API_KEY and "
                              "`venv/bin/pip install anthropic`")
    elif name == "ollama":
        out["configured"] = ollama_plan.configured()
        out["detail"] = f"{settings.ollama_model} at {ollama_plan.base_url()}"
        out["warning"] = (ollama_plan.warn_if_local() if out["configured"]
                          else "set OLLAMA_BASE_URL and OLLAMA_MODEL")
    else:
        out["configured"] = True      # the formula always works
        out["detail"] = "deterministic formula (plan.py)"
    return out


async def build(clips: list[dict], sources: dict, *,
                transcripts: dict | None = None,
                target_s: float = P.TARGET_S):
    """The edit plan for these clips, from whichever builder is configured.

    Returns (EditPlan, meta) exactly as each provider does, so a caller that
    only wants a plan can ignore the second half.
    """
    name = provider()
    if name == "none":
        return formula(clips, sources, "LLM_PROVIDER=none", target_s=target_s)

    if name == "ollama":
        from src.autopilot import ollama_plan as impl
    else:
        from src.autopilot import llm_plan as impl

    plan, meta = await impl.build(clips, sources, transcripts=transcripts,
                                  target_s=target_s)
    if meta.get("source") == "formula" and meta.get("reason"):
        # Logged once, here, so a provider quietly failing on every clip is
        # visible in one place rather than in three different modules' logs.
        log.warning("builder_fell_back", provider=name, reason=meta["reason"])
    return plan, meta
