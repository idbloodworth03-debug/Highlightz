"""Autopilot: an approved clip becomes a posted clip with nobody in the loop.

Owner (2026-09-15): "an autopilot thing for people with pro that can auto
grab accepted clips, edit them and post." Pro only, off by default, and
every step is one the product already does by hand:

    approve a clip        →  the user's own decision, in Clip Review
    edit it               →  src/autopilot/render.py, ffmpeg on the server,
                             one of the editor's templates, optional title
                             and captions (the browser editor is not
                             available here — there is no browser)
    put it in the queue   →  src/publish/schedule.py, source="autopilot",
                             at a time this module picks
    post it               →  src/publish/poster.py, to the platforms the
                             user connected

This package holds the CONFIG shape and the two pure decisions (when the
next post goes out, what its caption is). render.py builds the ffmpeg
command; runner.py strings it all together and is what the API calls.
"""

from __future__ import annotations

import re
import string
import time
from datetime import datetime, timedelta, timezone

TEMPLATES = ("full", "blur", "punch", "hook")
# The AUTO EDIT template (owner, 2026-09-28: the user "can choose what template
# they want for the auto edit or just use the suggested one"). These are the
# plan/graph renderer's own knobs, nothing invented: which framing, and whether
# the picture slides in and out with the whoosh. `suggested` is the owner's own
# design and the default. The legacy TEMPLATES above still drive the old
# renderer for accounts the plan-based edit has not reached yet.
#
# NO SLIDES, NO WHOOSH (owner, 2026-09-30: "get rid of the swoosh sound affect
# and the transition in at the beginning and end … I dont want that in there
# anymore"). `auto_edit.apply_template` takes them off every plan, so "clean",
# which differed only by lacking them, is gone; a saved "clean" normalises to
# "suggested", which is now the same edit.
EDIT_TEMPLATES = ("suggested", "fill")
EDIT_TEMPLATE_INFO = {
    "suggested": ("Suggested", "The whole clip over a blurred backdrop, with captions."),
    "fill":      ("Fill the screen", "The clip cropped to fill the vertical frame, with captions."),
}
TIMINGS = ("now", "spaced", "daily")
# Clipper or streamer. Both post ONE clip per video since 2026-09-23 (owner:
# "instead of combining clips just keep it only to one clip"); see
# plan.limits_for(), where a future difference between them would go.
MODES = ("clipper", "streamer")
PLATFORMS = ("youtube", "tiktok", "instagram")
# What Autopilot may post to BY ITSELF. TikTok is left out on purpose: its Direct
# Post rules require the person to choose who can view, the interactions and the
# commercial-content disclosure and to agree to its terms for each post, so a
# TikTok post is always made by hand from the posting screen. (A saved config
# that still lists it is quietly cleaned by `normalize`.)
AUTO_PLATFORMS = ("youtube", "instagram")
CAPTION_MAX = 2200
TITLE_MAX = 60

DEFAULT = {
    "enabled": False,
    "template": "full",
    "edit_template": "suggested",
    "mode": "clipper",
    "platforms": [],
    "timing": "spaced",
    "spacing_h": 4,            # for "spaced"
    "daily_at": "18:00",       # for "daily", in the user's local time
    "tz_offset_min": 0,        # the browser's -getTimezoneOffset(); daily_at is local
    "caption_text": "{title} #{channel} #twitchclips",
    "title": True,             # burn the clip's title onto the video (LEGACY renderer)
    "captions": False,         # burn auto-captions (LEGACY renderer; needs CAPTIONS_ENABLED)
    # AUTO EDIT's own switches (owner, 2026-09-28: the preset is the whole clip
    # with CAPTIONS AT THE BOTTOM and nothing at the top). Separate from the two
    # above on purpose: `title` defaults ON for the legacy renderer, and passing
    # it into the plan-based edit put the clip's title at the top of every
    # Autopilot video — the words at the top the owner saw.
    "edit_captions": True,
    "edit_title": False,
}


def normalize(raw: dict | None) -> dict:
    """A config the rest of the package can trust: every key present, every
    value in range. Unknown keys are dropped."""
    raw = raw or {}
    cfg = dict(DEFAULT)
    cfg["enabled"] = bool(raw.get("enabled", False))
    cfg["template"] = raw.get("template") if raw.get("template") in TEMPLATES else DEFAULT["template"]
    cfg["edit_template"] = (raw.get("edit_template") if raw.get("edit_template") in EDIT_TEMPLATES
                            else DEFAULT["edit_template"])
    cfg["mode"] = raw.get("mode") if raw.get("mode") in MODES else DEFAULT["mode"]
    cfg["platforms"] = [p for p in (raw.get("platforms") or []) if p in AUTO_PLATFORMS][:3]
    cfg["timing"] = raw.get("timing") if raw.get("timing") in TIMINGS else DEFAULT["timing"]
    try:
        cfg["spacing_h"] = int(min(48, max(1, int(raw.get("spacing_h", DEFAULT["spacing_h"])))))
    except (TypeError, ValueError):
        cfg["spacing_h"] = DEFAULT["spacing_h"]
    daily = str(raw.get("daily_at") or DEFAULT["daily_at"])
    cfg["daily_at"] = daily if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", daily) else DEFAULT["daily_at"]
    try:
        cfg["tz_offset_min"] = int(max(-840, min(840, int(raw.get("tz_offset_min", 0)))))
    except (TypeError, ValueError):
        cfg["tz_offset_min"] = 0
    cfg["caption_text"] = str(raw.get("caption_text") if raw.get("caption_text") is not None
                              else DEFAULT["caption_text"])[:CAPTION_MAX]
    cfg["title"] = bool(raw.get("title", DEFAULT["title"]))
    cfg["captions"] = bool(raw.get("captions", DEFAULT["captions"]))
    cfg["edit_captions"] = bool(raw.get("edit_captions", DEFAULT["edit_captions"]))
    cfg["edit_title"] = bool(raw.get("edit_title", DEFAULT["edit_title"]))
    return cfg


def next_due(cfg: dict, now: float | None = None, last_due: float = 0.0) -> float:
    """When the next Autopilot post goes out, in epoch seconds.

    "now"    → a minute from now (the poster's loop is 30 s; a minute gives
               the tab time to show the card before it is already posting).
    "spaced" → spacing_h after the later of now and the last Autopilot post,
               so five approvals in a row become five posts a few hours apart
               rather than five posts at once.
    "daily"  → the next daily_at (user's local time) that is after both now
               and the last Autopilot post, one post per day.
    """
    now = time.time() if now is None else now
    floor = max(now + 60, last_due + 1 if last_due else 0)
    if cfg["timing"] == "now":
        return now + 60
    if cfg["timing"] == "spaced":
        return max(now + 60, last_due + cfg["spacing_h"] * 3600 if last_due else 0)
    # daily
    tz = timezone(timedelta(minutes=cfg["tz_offset_min"]))
    hh, mm = (int(x) for x in cfg["daily_at"].split(":"))
    base = datetime.fromtimestamp(floor, tz)
    cand = base.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand.timestamp() <= floor:
        cand += timedelta(days=1)
    if last_due:
        # One per day: if the last one already sits on cand's day, move on.
        last_local = datetime.fromtimestamp(last_due, tz)
        if last_local.date() >= cand.date():
            cand = cand.replace(year=last_local.year, month=last_local.month, day=last_local.day) + timedelta(days=1)
    return cand.timestamp()


class _Safe(dict):
    def __missing__(self, key):
        return ""


def caption_for(cfg: dict, clip: dict) -> str:
    """The caption from the user's template. {title} {channel} {game}
    {platform} are filled; anything else is blank rather than an error."""
    title = str(clip.get("clip_title") or clip.get("stream_title") or "").strip()
    fields = _Safe(title=title, channel=str(clip.get("channel") or ""),
                   game=str(clip.get("game") or "").replace(" ", ""),
                   platform=str(clip.get("platform") or ""))
    try:
        out = string.Formatter().vformat(cfg["caption_text"], (), fields)
    except (ValueError, IndexError):
        out = cfg["caption_text"]
    out = re.sub(r"[ \t]+", " ", out).strip()
    return out[:CAPTION_MAX]


def title_for(cfg: dict, clip: dict) -> str:
    if not cfg["title"]:
        return ""
    t = str(clip.get("clip_title") or clip.get("stream_title") or "").strip()
    return t[:TITLE_MAX]
