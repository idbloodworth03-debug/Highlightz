"""Why the server refused another stream — the real numbers, from the real box.

_check_server_capacity refuses on two different rules and they mean opposite
things. One says the box is full; the other says the box is nearly full and you
personally are the heaviest user. The error text differs but the numbers behind
it do not appear anywhere, so this prints them.

    /opt/highlightz/venv/bin/python scripts/capacity.py

Read-only.
"""
import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from config.settings import settings                      # noqa: E402
from src.dashboard import api                             # noqa: E402

# TWO caps. The hardware ceiling governs LIVE streams and is enforced at
# go-live; this file's `cap` is the REGISTERED bound, which is what
# _check_server_capacity refuses on.
live_cap = max(1, settings.max_concurrent_streams)
live_now = api.live_stream_count()
cap   = max(1, settings.max_registered_streams)
total = len(api._streams)
by_user = collections.Counter(k.split(":", 1)[0] for k in api._streams)
users = len(by_user) or 1
reserve = max(1, int(cap * api._CAPACITY_RESERVE_FRAC))
fair = max(1, cap // users)

names = {}
try:
    from src.auth import users as user_store
    for u in user_store.get_all():
        names[u["id"]] = u.get("twitch_login") or u.get("username") or u["id"]
except Exception as exc:
    print(f"(could not read the user store: {exc})")

print("=" * 68)
print("SERVER CAPACITY")
print("=" * 68)
print(f"  LIVE ceiling          : {live_cap}      <- the hardware limit (cores x 6)")
print(f"  live right now        : {live_now}")
print(f"  live headroom         : {live_cap - live_now}")
print()
print(f"  registered ceiling    : {cap}      <- how many channels may be ADDED")
print(f"  streams running now    : {total}")
print(f"  headroom               : {cap - total}")
print(f"  users with streams     : {users}")
print(f"  reserve threshold      : {reserve}   (headroom must exceed this to skip sharing)")
print(f"  fair share per user    : {fair}   (cap // users)")

print()
print("  per user:")
for uid, n in by_user.most_common():
    flag = "  <- over fair share, refused next add" if n >= fair else ""
    print(f"      {names.get(uid, uid)[:24]:<24}{n:>4}{flag}")

print()
print("=" * 68)
print("WHAT HAPPENS ON THE NEXT ADD")
print("=" * 68)
if total >= cap:
    print("  503 THE BOX IS FULL. Nobody can add a stream, whatever their plan.")
    print("  Raising MAX_CONCURRENT_STREAMS is the only thing that changes this,")
    print("  and every stream costs a chat socket, an evaluation loop and (with")
    print("  audio on) a streamlink + ffmpeg on ONE vCPU. Check load first.")
elif cap - total > reserve:
    print("  ALLOWED for everyone — there is plenty of room, so plan limits")
    print("  govern and the sharing rule does not run at all.")
else:
    print(f"  SHARING IS ACTIVE. Headroom is {cap - total}, which is not more than the")
    print(f"  reserve of {reserve}, so anyone at or above {fair} streams is refused")
    print("  while anyone below it can still add. This is the guard working as")
    print("  designed: it degrades onto the heaviest user rather than onto the")
    print("  newest customer, who would otherwise be refused their first channel.")
    over = [u for u, n in by_user.items() if n >= fair]
    if over:
        print(f"  Currently refused: {', '.join(names.get(u, u) for u in over)}")

print()
print("  Note: this reads the CURRENT process state via the saved streams file.")
print("  Numbers move as streams start and stop.")
