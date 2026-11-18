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

ORDER = ("youtube", "tiktok", "instagram", "x")
LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "x": "X"}
# The platforms Autopilot posts to by itself. TikTok is never one: its
# Direct Post rules put the choices on the person, every post.
AUTO = ("youtube", "instagram", "x")
# Platforms that do not exist ANYWHERE on the site until the operator has put
# their app keys in: no chip, no "coming soon", no destination to tick. X was
# added on 2026-11-18 and costs money per post; announcing it as "coming
# soon" before the owner has an X app would be a promise with nothing behind
# it. The original three were announced long ago and stay announced.
OPTIONAL = ("x",)


def offered(platform: str) -> bool:
    """Does this platform exist on the site at all right now?"""
    if platform not in OPTIONAL:
        return True
    from src.publish import providers
    p = providers.get(platform)
    return bool(p and p.configured())


def public_platforms() -> tuple[str, ...]:
    want = {p.strip().lower() for p in str(settings.public_platforms or "").split(",")}
    return tuple(p for p in ORDER if p in want and offered(p))


def live_platforms() -> tuple[str, ...]:
    """Open to an ordinary account right now: released AND public."""
    return public_platforms() if settings.uploads_enabled else ()


def held_platforms() -> tuple[str, ...]:
    live = set(live_platforms())
    return tuple(p for p in ORDER if p not in live and offered(p))


def auto_posting_live() -> bool:
    """Can Autopilot actually post anything for an ordinary account?"""
    return any(p in AUTO for p in live_platforms())


def auto_live() -> tuple[str, ...]:
    """The platforms posted to BY ITSELF that are open right now. Two states
    were not enough once YouTube opened ahead of Instagram (2026-11-18): copy
    that only knew "TikTok alone" or "everything" promised Instagram the day
    YouTube went live. Anything that names platforms names these."""
    return tuple(p for p in live_platforms() if p in AUTO)


def all_live() -> bool:
    """Every platform is open: the only state where "all three" is true."""
    return not held_platforms()


def youtube_private() -> bool:
    """YouTube is open to accounts but has not audited the app, so what it
    uploads lands private (YOUTUBE_AUDITED, config/settings.py). Every page
    that says "posts to YouTube" says this beside it while it is true."""
    return "youtube" in live_platforms() and not settings.youtube_audited


def youtube_note() -> str:
    """The sentence while it applies to ordinary accounts, else "" (/me)."""
    return YOUTUBE_PRIVATE_NOTE if youtube_private() else ""


# The one sentence, so the landing page, the FAQ, the briefs and the dashboard
# cannot drift apart on it. It does not say the video can be made public by
# hand: whether YouTube allows that for an unaudited app is not known.
YOUTUBE_PRIVATE_NOTE = ("Until Google finishes reviewing Highlightz, YouTube keeps "
                        "what it uploads private on your channel.")


def open_for(platform: str, user: dict | None) -> bool:
    """May this account connect and post to `platform`?"""
    if user and (user.get("is_admin") or user.get("early_access")):
        return True
    return platform in public_platforms()


def join(ids) -> str:
    """("youtube", "instagram") -> "YouTube and Instagram"."""
    return join_words([LABELS[p] for p in ids])


def join_words(names) -> str:
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]
