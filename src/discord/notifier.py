"""
Discord webhook notifier — posts approved clips as rich embeds.
"""

import asyncio
import urllib.request
import urllib.error
import json
import structlog

log = structlog.get_logger(__name__)

_BASE_URL = "https://highlightz.app"

# Virality colour gradient: grey → green → yellow → orange → red
def _virality_color(score: float) -> int:
    if score >= 80:
        return 0xEB0400   # red — viral
    if score >= 60:
        return 0xFF6B00   # orange — very hot
    if score >= 40:
        return 0xFFD700   # yellow — hot
    if score >= 20:
        return 0x00B86B   # green — decent
    return 0x5C5C5C       # grey — low


def _virality_label(score: float) -> str:
    if score >= 80:
        return "🔥 VIRAL"
    if score >= 60:
        return "🔥 Very Hot"
    if score >= 40:
        return "⚡ Hot"
    if score >= 20:
        return "✅ Decent"
    return "📎 Low"


async def post_clip(webhook_url: str, clip: dict) -> None:
    """Post an approved clip embed to a Discord webhook. Fire-and-forget."""
    channel = clip.get("channel", "unknown")
    title = clip.get("clip_title") or f"Clip from {channel}"
    score = clip.get("trigger_score", 0.0)
    virality = clip.get("virality_score", 0.0)
    game = clip.get("game", "")
    storage_url = clip.get("storage_url", "")
    clip_id = clip.get("id", "")

    clip_link = f"{_BASE_URL}/clip-file?path={storage_url}" if storage_url else None

    fields = [
        {"name": "Channel", "value": channel, "inline": True},
        {"name": "Score", "value": str(round(score, 1)), "inline": True},
        {"name": "Virality", "value": f"{_virality_label(virality)} ({round(virality, 1)})", "inline": True},
    ]
    if game:
        fields.append({"name": "Game", "value": game, "inline": True})

    embed = {
        "title": title,
        "color": _virality_color(virality),
        "fields": fields,
        "footer": {"text": "Highlightz • highlightz.app"},
    }
    if clip_link:
        embed["url"] = clip_link
        embed["description"] = f"[▶ Watch Clip]({clip_link})"

    payload = {"embeds": [embed]}

    try:
        await asyncio.get_event_loop().run_in_executor(None, lambda: _post_sync(webhook_url, payload))
        log.info("discord_clip_posted", clip_id=clip_id, channel=channel)
    except Exception as exc:
        log.warning("discord_post_failed", clip_id=clip_id, error=str(exc))


def _post_sync(webhook_url: str, payload: dict) -> None:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "Highlightz/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        if resp.status not in (200, 204):
            raise RuntimeError(f"Discord returned {resp.status}")
