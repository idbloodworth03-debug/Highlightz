"""Where a finished clip is going, and whether it will actually be accepted.

DELIBERATELY NOT AN API INTEGRATION. We do not post on the user's behalf, and
that is a design decision, not a gap: posting through TikTok's Content Posting
API, Instagram's Content Publishing API or YouTube's Data API all require the
app to pass platform review first (weeks of calendar time), and YouTube's
default quota of 10,000 units/day against 1600 per upload would cap the ENTIRE
app at six uploads a day. Handing the user a correctly-shaped file plus a
one-tap share needs none of that and works today.

So what this module is for: catching the rejection BEFORE the user uploads.
Every one of these limits is something a creator otherwise discovers by
exporting a clip, opening an app, waiting for an upload, and being told no.

VERIFY BEFORE TRUSTING — these change, and a stale limit here is worse than no
limit because it is confidently wrong. Last checked 2026-08-02. When one moves,
change it here; nothing else hard-codes them.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    # Seconds. `ideal_max` is the limit that matters for reach (e.g. the cutoff
    # to be treated as a Short), `hard_max` is what the uploader will refuse.
    # They are different numbers and conflating them either scares users off
    # valid clips or lets through ones that lose their format.
    ideal_max_s: float
    hard_max_s: float
    caption_max: int
    preferred_ratio: str
    upload_url: str
    note: str


# A Twitch clip is ~30s, so every one of these fits comfortably by default.
# The limits bite on VOD-sourced and stitched content, which is exactly where a
# silent rejection is most annoying.
PLATFORMS: tuple[Platform, ...] = (
    Platform(
        id="tiktok", label="TikTok",
        ideal_max_s=60.0, hard_max_s=600.0, caption_max=2200,
        preferred_ratio="9:16",
        upload_url="https://www.tiktok.com/upload",
        note="Vertical, sound on. Under 60s tends to loop and retain best.",
    ),
    Platform(
        id="instagram", label="Instagram Reels",
        ideal_max_s=90.0, hard_max_s=180.0, caption_max=2200,
        preferred_ratio="9:16",
        upload_url="https://www.instagram.com/",
        note="Reels only — a 9:16 clip posted as a feed video gets cropped.",
    ),
    Platform(
        id="youtube", label="YouTube Shorts",
        ideal_max_s=60.0, hard_max_s=180.0, caption_max=100,
        preferred_ratio="9:16",
        upload_url="https://www.youtube.com/upload",
        note="Over the Shorts cutoff it is published as a normal video, not a "
             "Short — different feed, different reach. Caption limit here is "
             "the TITLE limit, which is what people paste into.",
    ),
)

BY_ID = {p.id: p for p in PLATFORMS}


def check_fit(platform_id: str, duration_s: float, ratio: str,
              caption: str = "") -> list[str]:
    """Problems this clip would hit on this platform. Empty list = good to go.

    Ordered worst-first: a hard rejection matters more than losing Shorts
    eligibility, which matters more than an aspect-ratio crop.
    """
    p = BY_ID.get(platform_id)
    if not p:
        return [f"Unknown platform {platform_id!r}"]

    issues: list[str] = []
    if duration_s > p.hard_max_s:
        issues.append(
            f"{duration_s:.0f}s is over {p.label}'s {p.hard_max_s:.0f}s limit — "
            f"it will be rejected. Trim it first.")
    elif duration_s > p.ideal_max_s:
        if p.id == "youtube":
            issues.append(
                f"{duration_s:.0f}s is over the {p.ideal_max_s:.0f}s Shorts "
                f"cutoff — this posts as a normal video, not a Short.")
        else:
            issues.append(
                f"{duration_s:.0f}s is over {p.ideal_max_s:.0f}s, where "
                f"{p.label} reach usually drops off.")
    if ratio and ratio != p.preferred_ratio:
        issues.append(
            f"{p.label} expects {p.preferred_ratio}; {ratio} gets cropped or "
            f"letterboxed.")
    if caption and len(caption) > p.caption_max:
        issues.append(
            f"Caption is {len(caption)} characters; {p.label} allows "
            f"{p.caption_max}.")
    return issues


def public_specs() -> list[dict]:
    """What the dashboard needs. Plain dicts so this crosses JSON unchanged."""
    return [asdict(p) for p in PLATFORMS]
