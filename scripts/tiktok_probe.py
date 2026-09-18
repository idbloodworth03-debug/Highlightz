"""What TikTok says this account may post. Read-only, run on PRODUCTION.

    /opt/highlightz/venv/bin/python scripts/tiktok_probe.py

WHY THIS EXISTS. `video/init` refused a post with "Please review our
integration guidelines", which is TikTok's generic message and names nothing.
The provider picks a privacy level from whatever `creator_info` offers and the
most likely explanation is that it picked one an unaudited app is not allowed
to use — but that is a guess until the real response is in hand, and guessing
is how you "fix" the wrong thing.

So this asks the one question that settles it, against the real token on the
real server: what does creator_info actually return, and which level would the
provider choose from it? It only READS (creator_info is a query; nothing is
posted, nothing is initialised, no publish session is opened).

Prints no token. The account's display name is already the operator's own.
"""
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings                      # noqa: E402
from src.publish import connections as pub_conns          # noqa: E402
from src.publish.providers import _http                   # noqa: E402
from src.publish.providers.tiktok import _CREATOR, _PRIVACY_ORDER   # noqa: E402


async def main() -> int:
    key = settings.tiktok_client_key or ""
    print(f"client key : {key or '(unset)'}"
          f"{'   [SANDBOX]' if key.startswith('sb') else '   [production]' if key else ''}")

    pub_conns._load()
    conns = [c for (_u, p), c in pub_conns._conns.items() if p == "tiktok"]
    if not conns:
        print("No TikTok connection stored. Connect one in the Scheduler first.")
        return 1
    conn = conns[0]
    print(f"account    : {conn.account_name or conn.account_id or '(unnamed)'}")
    print(f"scopes     : {conn.scopes or '(none recorded)'}")
    if conn.last_error:
        print(f"last error : {conn.last_error}")

    r = await _http.request("POST", _CREATOR, headers={
        "Authorization": "Bearer " + conn.access_token,
        "Content-Type": "application/json; charset=UTF-8"}, json={})
    print(f"\ncreator_info HTTP {r.status}")
    try:
        payload = json.loads(r.text)
    except Exception:
        print(r.text[:2000])
        return 1

    err = payload.get("error") or {}
    if str(err.get("code") or "") not in ("", "ok"):
        print(f"  error code : {err.get('code')}")
        print(f"  message    : {err.get('message')}")
        return 1

    d = payload.get("data") or {}
    options = d.get("privacy_level_options") or []
    print(f"  privacy_level_options      : {options}")
    print(f"  max_video_post_duration_sec: {d.get('max_video_post_duration_sec')}")
    for k in ("comment_disabled", "duet_disabled", "stitch_disabled"):
        if k in d:
            print(f"  {k:<27}: {d[k]}")

    chosen = next((p for p in _PRIVACY_ORDER if p in options),
                  options[0] if options else "SELF_ONLY")
    print(f"\n  the provider would ask for : {chosen}")
    if chosen != "SELF_ONLY":
        print("  -> An unaudited app may only post SELF_ONLY. If creator_info still")
        print("     offers the public levels, asking for one is what init rejected.")
    else:
        print("  -> Already the private level, so the refusal is something else.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
