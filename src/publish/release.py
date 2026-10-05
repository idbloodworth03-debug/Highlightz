"""Which posting platforms are open to ordinary accounts (owner, 2026-10-05).

TikTok passed its audit; Meta (Instagram) and Google (YouTube) have not
finished theirs. The owner released the Clip Editor, Scheduler and Autopilot
to Pro with TikTok and kept YouTube and Instagram "coming soon": an
unapproved Meta app refuses every account that is not a listed tester, and an
unverified Google app shows a warning screen and a six-uploads-a-day quota.

`PUBLIC_PLATFORMS` (comma list, default "tiktok") is the switch. Admins and
early-access accounts see every configured platform regardless, so testing
and app review keep working. Add "instagram" the day Meta approves, and
"youtube" the day Google does; one restart moves the dashboard and every
public page together.
"""

from __future__ import annotations

from config.settings import settings

ORDER = ("youtube", "tiktok", "instagram")
LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram"}
# The platforms Autopilot posts to by itself. TikTok is never one: its
# Direct Post rules put the choices on the person, every post.
AUTO = ("youtube", "instagram")


def public_platforms() -> tuple[str, ...]:
    want = {p.strip().lower() for p in str(settings.public_platforms or "").split(",")}
    return tuple(p for p in ORDER if p in want)


def live_platforms() -> tuple[str, ...]:
    """Open to an ordinary account right now: released AND public."""
    return public_platforms() if settings.uploads_enabled else ()


def held_platforms() -> tuple[str, ...]:
    live = set(live_platforms())
    return tuple(p for p in ORDER if p not in live)


def auto_posting_live() -> bool:
    """Can Autopilot actually post anything for an ordinary account?"""
    return any(p in AUTO for p in live_platforms())


def open_for(platform: str, user: dict | None) -> bool:
    """May this account connect and post to `platform`?"""
    if user and (user.get("is_admin") or user.get("early_access")):
        return True
    return platform in public_platforms()


def join(ids) -> str:
    """("youtube", "instagram") -> "YouTube and Instagram"."""
    names = [LABELS[p] for p in ids]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
