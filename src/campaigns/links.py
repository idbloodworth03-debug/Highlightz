"""Entry links (owner, 2026-10-25: "let them post their entry links if they
want. Lets do that so it makes it easier to grade and score people and find
the winners ... But dont make it forced").

A clipper who joined a campaign can enter a post they made themselves,
outside Highlightz, by pasting its link on the campaign page. It is then an
entry like a post made from Highlightz: counted by an admin from the post,
on the same leaderboard, judged by the same rules. Optional: posting from
Highlightz (Pro) still enters by itself, and nobody has to add a link.

This module only reads a link: the platform it is on, a clean copy of it
(no tracking query), and a key that is the same however the link was copied
(www or not, mobile or not, a trailing slash, a Short as /shorts/ or as
watch?v=), so one post cannot be entered twice. A TikTok short link
(vm.tiktok.com/...) cannot be matched to the long form without asking
TikTok, so the two are different keys; an admin sees both links.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

MAX_URL = 400
LABELS = {"tiktok": "TikTok", "instagram": "Instagram", "youtube": "YouTube"}

_TT_USER = r"[A-Za-z0-9_.]{1,60}"
_TT_SHORT = r"[A-Za-z0-9]{4,24}"
_IG_CODE = r"[A-Za-z0-9_-]{5,40}"
_YT_ID = r"[A-Za-z0-9_-]{6,20}"


class LinkError(ValueError):
    """Not a link to a post we can count; the message says why."""


def parse(url: str) -> tuple[str, str, str]:
    """(platform, key, clean link) for a link to a post, or LinkError."""
    raw = str(url or "").strip()
    if not raw:
        raise LinkError("Paste the link to your post.")
    if len(raw) > MAX_URL or any(ch.isspace() for ch in raw):
        raise LinkError("That does not look like a link to a post.")
    if "://" not in raw:
        raw = "https://" + raw
    try:
        u = urlsplit(raw)
        host = (u.hostname or "").lower()
    except ValueError:
        raise LinkError("That does not look like a link to a post.")
    if u.scheme not in ("http", "https"):
        raise LinkError("That does not look like a link to a post.")
    for prefix in ("www.", "m."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    path = u.path.rstrip("/")

    if host == "tiktok.com":
        m = re.fullmatch(rf"/@({_TT_USER})/(?:video|photo)/(\d{{6,25}})", path)
        if m:
            return "tiktok", "tiktok:" + m.group(2), f"https://www.tiktok.com/@{m.group(1)}/video/{m.group(2)}"
        m = re.fullmatch(rf"/t/({_TT_SHORT})", path)
        if m:
            return "tiktok", "tiktok:t/" + m.group(1), f"https://www.tiktok.com/t/{m.group(1)}"
    if host in ("vm.tiktok.com", "vt.tiktok.com"):
        m = re.fullmatch(rf"/({_TT_SHORT})", path)
        if m:
            return "tiktok", f"tiktok:{host[:2]}/{m.group(1)}", f"https://{host}/{m.group(1)}"
    if host == "instagram.com":
        m = re.fullmatch(rf"(?:/{_TT_USER})?/(reels?|p|tv)/({_IG_CODE})", path)
        if m:
            kind = "reel" if m.group(1).startswith("reel") else m.group(1)
            return "instagram", "instagram:" + m.group(2), f"https://www.instagram.com/{kind}/{m.group(2)}/"
    if host == "youtube.com":
        m = re.fullmatch(rf"/shorts/({_YT_ID})", path)
        if m:
            return "youtube", "youtube:" + m.group(1), f"https://www.youtube.com/shorts/{m.group(1)}"
        if path == "/watch":
            v = (parse_qs(u.query).get("v") or [""])[0]
            if re.fullmatch(_YT_ID, v):
                return "youtube", "youtube:" + v, f"https://www.youtube.com/watch?v={v}"
    if host == "youtu.be":
        m = re.fullmatch(rf"/({_YT_ID})", path)
        if m:
            return "youtube", "youtube:" + m.group(1), f"https://youtu.be/{m.group(1)}"
    raise LinkError("That is not a link to a TikTok, Instagram or YouTube post.")


def key_of(url: str) -> str:
    """The post's key, or "" for anything that is not a post link (an entry
    still on its way has no link yet)."""
    try:
        return parse(url)[1]
    except LinkError:
        return ""
