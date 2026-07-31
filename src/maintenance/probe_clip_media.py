"""
Read-only probe: is a Twitch clip's MP4 reachable, and can a BROWSER read it?

This answers one product question and nothing else. TikTok's and Instagram's
publishing APIs take either raw bytes or a URL on a domain you have verified
you own — neither accepts a twitch.tv link. So publishing a clip requires
possessing the file, and the only question is whether there is a path to it we
are willing to use.

    venv/bin/python -m src.maintenance.probe_clip_media <channel>

It touches nothing: Get Clips (documented Helix, app token) plus one HEAD per
candidate URL. It creates nothing, stores nothing, and changes no state.

WHY THIS EXISTS RATHER THAN A ONE-LINER: the obvious shell version gets two
things wrong, and both produce a confident-looking 403 that means nothing.

  1. The media path is NOT the clip slug. A slug looks like
     `AwkwardHelplessSalamanderSwiftRage`; the file lives at a different
     internal id that only appears in `thumbnail_url`. Curling
     `<slug>.mp4` 403s on every clip that has ever existed.
  2. Our own `clips.json` is not a usable source for this. Every stored record
     carries a VOD thumbnail (`cf_vods/.../thumb0-1280x720.jpg`), not a clip
     thumbnail, so nothing in it can be turned into an MP4 URL. The value has
     to come live from Get Clips.

The three answers, in order of how much they'd let us build:

  200 + access-control-allow-origin
      The user's own BROWSER can fetch their clip and hand it to /uploads.
      Our server never touches Twitch's CDN. One button, and the request comes
      from the user's client, acting on the user's own content.
  200, no access-control header
      Only a server-side fetch works — the StreamLadder approach. That is a
      grey-area call requiring the ToS and four marketing claims to be
      rewritten first (see HANDOFF, "No video recording/re-hosting").
  403/404
      The derivation does not hold for these clips. Nothing to decide.

Either way this changes NOTHING about listing a user's clips: Get Clips is
documented, we already page it, and importing someone's whole clip catalogue
as metadata + embeds needs none of this.
"""

import asyncio
import re
import sys

import aiohttp

from config.settings import settings
from src.output.twitch_clips import HELIX_BASE, _get_app_token, resolve_broadcaster_id

# thumbnail_url -> MP4. The documented field ends in `-preview-<W>x<H>.jpg`;
# the video sits at the same path with that suffix replaced.
_PREVIEW_RE = re.compile(r"-preview-\d+x\d+\.jpg$")

ORIGIN = "https://highlightz.app"


def mp4_from_thumbnail(thumb: str) -> str | None:
    """Derive the candidate MP4 URL, or None when the shape doesn't match.

    Returning None rather than guessing matters: a VOD thumbnail passed
    through a naive `.replace('.jpg', '.mp4')` yields a URL that 403s, which
    reads as "Twitch blocked us" when it actually means "wrong input".
    """
    if not thumb or not _PREVIEW_RE.search(thumb):
        return None
    return _PREVIEW_RE.sub(".mp4", thumb)


async def probe(channel: str, limit: int = 3) -> int:
    if not settings.twitch_client_id:
        print("TWITCH_CLIENT_ID is not set — cannot call Helix.")
        return 2

    async with aiohttp.ClientSession() as session:
        bid = await resolve_broadcaster_id(channel)
        if not bid:
            print(f"Could not resolve channel {channel!r}.")
            return 2

        token = await _get_app_token(session)
        headers = {"Client-Id": settings.twitch_client_id,
                   "Authorization": f"Bearer {token}"}
        async with session.get(f"{HELIX_BASE}/clips", headers=headers,
                               params={"broadcaster_id": bid, "first": limit}) as r:
            if r.status != 200:
                print(f"Get Clips failed: HTTP {r.status}")
                return 2
            clips = (await r.json()).get("data", [])

        if not clips:
            print(f"{channel} has no clips to probe.")
            return 2

        print(f"{channel}: probing {len(clips)} clip(s)\n")
        verdicts = []
        for c in clips:
            thumb = c.get("thumbnail_url", "")
            print(f"  clip     {c.get('id')}  ({c.get('view_count', 0)} views)")
            print(f"  thumb    {thumb}")
            url = mp4_from_thumbnail(thumb)
            if not url:
                print("  -> thumbnail is not in the -preview- shape; cannot derive an MP4\n")
                verdicts.append("no-derivation")
                continue
            print(f"  mp4?     {url}")
            try:
                # HEAD only: we are asking whether it is reachable, not
                # downloading anything.
                async with session.head(url, headers={"Origin": ORIGIN},
                                        allow_redirects=True) as m:
                    acao = m.headers.get("Access-Control-Allow-Origin")
                    print(f"  -> HTTP {m.status}"
                          f"  type={m.headers.get('Content-Type', '?')}"
                          f"  len={m.headers.get('Content-Length', '?')}")
                    print(f"  -> Access-Control-Allow-Origin: {acao or '(none)'}\n")
                    if m.status != 200:
                        verdicts.append("unreachable")
                    elif acao in ("*", ORIGIN):
                        verdicts.append("browser-ok")
                    else:
                        verdicts.append("server-only")
            except Exception as exc:
                print(f"  -> request failed: {exc}\n")
                verdicts.append("unreachable")

    best = ("browser-ok" if "browser-ok" in verdicts
            else "server-only" if "server-only" in verdicts
            else "no-derivation" if "no-derivation" in verdicts
            else "unreachable")
    print("=" * 68)
    if best == "browser-ok":
        print("VERDICT: browser-side fetch WORKS.")
        print("  The user's own browser can pull their clip and post it to")
        print("  /uploads. Our server never touches Twitch's CDN. This is the")
        print("  one-click import, and it is the option worth building.")
    elif best == "server-only":
        print("VERDICT: server-side only — no CORS header.")
        print("  A browser cannot read the bytes, so one-click import is off")
        print("  the table. Reaching the file at all would mean fetching it")
        print("  from our server: grey area, and the ToS plus four marketing")
        print("  claims must be rewritten BEFORE any such code ships.")
    elif best == "no-derivation":
        print("VERDICT: inconclusive — thumbnails are not in the expected shape.")
        print("  Get Clips returned clips whose thumbnail_url has no")
        print("  -preview-<W>x<H>.jpg suffix, so no MP4 URL can be derived.")
    else:
        print("VERDICT: not reachable.")
        print("  The derivation does not hold for these clips. There is no")
        print("  file-grabbing path here, whatever the appetite for one.")
    print()
    print("Unaffected either way: listing a user's clips (title, thumbnail,")
    print("views, embed) is documented Helix and works today.")
    return 0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__.strip().splitlines()[0])
        print("\nusage: python -m src.maintenance.probe_clip_media <channel>")
        return 2
    return asyncio.run(probe(sys.argv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
