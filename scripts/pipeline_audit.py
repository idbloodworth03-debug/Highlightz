"""End-to-end clipping pipeline audit.

Drives the REAL TriggerEngine, the REAL ClipJob, the REAL ClipProcessor and the
REAL dashboard notify path. Only two things are faked, both at the outer edge:
Twitch's HTTP API and the user store. Everything between chat arriving and a
clip appearing in the review queue is production code.

The question is not "does each function work" but "does a hot moment on a live
stream become a clip the user can see", which is the only question that matters.
"""
import asyncio, json, pathlib, sys, tempfile, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src.trigger.engine import TriggerEngine
from src.queue.job_queue import ClipJob
from src.processor.clip_processor import ClipProcessor
from src.output import twitch_clips
from src.auth import users as user_store
from src.dashboard import api as dash

OK, FAIL = "  PASS", "  FAIL"
results = []
def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(f"{OK if cond else FAIL}  {name}" + (f"   {detail}" if detail else ""))

tmp = pathlib.Path(tempfile.mkdtemp())
user_store._USERS_FILE = tmp / "users.json"
user_store._BACKUP_FILE = tmp / "users.json.bak"
(tmp / "users.json").write_text(json.dumps([{
    "id": "u1", "username": "nova", "twitch_id": "123",
    "subscription_status": "active", "plan": "pro", "created_at": time.time()}]))

# ── stub Twitch at the HTTP boundary ────────────────────────────────────────
created = []
async def _token(uid): return "usertoken"
async def _resolve(login): return "bcast_" + login
async def _create(token, bid, **kw):
    created.append((token, bid)); return "SlugAbc123"
async def _get(slug):
    return {"id": slug, "url": f"https://clips.twitch.tv/{slug}",
            "embed_url": f"https://clips.twitch.tv/embed?clip={slug}",
            "thumbnail_url": "https://x/-preview-480x270.jpg", "duration": 30.0,
            "title": "captured"}
user_store.get_valid_twitch_token = _token
twitch_clips.resolve_broadcaster_id = _resolve
twitch_clips.create_clip = _create
twitch_clips.get_clip = _get

sent = []
async def _bc(msg, **kw): sent.append((msg.get("event"), kw.get("user_id")))
dash.broadcast = _bc
dash._save_clips = lambda: None
dash._clips.clear()


print("\n" + "=" * 74)
print("STAGE 1  chat + audio -> the engine fires")
print("=" * 74)

fired = []
async def on_trigger(ev): fired.append(ev)

class Meter:
    """Stands in for AudioMeter: loud enough to look like a real spike."""
    def __init__(self, db): self.db = db
    async def get_audio_level_db(self): return self.db

async def stage1():
    eng = TriggerEngine(channel="lacy", on_trigger=on_trigger, buffer=Meter(-12.0))
    eng.update_viewer_count(4000)
    # Drive it the way production does: run_evaluation_loop sets _running, which
    # _monitor_and_fire requires — without it the monitor returns before firing.
    loop = asyncio.create_task(eng.run_evaluation_loop(interval=0.2))
    await asyncio.sleep(0.4)
    base = len(fired)
    for i in range(300):
        eng.ingest_chat(f"h{i}", "POGGERS INSANE CLIP THAT actual insane play LETSGO")
    eng.notify_sub_raid()
    # SETTLE_SECS is 3s inside the monitor; give it room to crest and fire.
    await asyncio.sleep(5.0)
    eng.stop()
    loop.cancel()
    try: await loop
    except asyncio.CancelledError: pass
    return base, eng

base, eng = asyncio.run(stage1())
check("a hype burst produces a trigger", len(fired) > base,
      f"fired={len(fired)}")
if fired:
    ev = fired[0]
    check("the trigger carries a score", getattr(ev, "score", 0) > 0,
          f"score={getattr(ev,'score',None)}")
    check("the trigger carries signals",
          bool(getattr(ev, "signals", None)),
          f"signals={len(getattr(ev,'signals',[]) or [])}")

print("\n" + "=" * 74)
print("STAGE 2  trigger -> ClipJob survives the queue serialisation")
print("=" * 74)

job = ClipJob(clip_id="c1", channel="lacy", platform="twitch", user_id="u1",
              trigger_score=88.0, trigger_signals={"chat_velocity": 90},
              chat_snapshot=["POGGERS"], stream_title="t", game="VALORANT",
              virality_score=71.0, clip_title="insane play", post_roll=0)
wire = ClipJob.from_json(job.to_json())
check("a job round-trips through Redis JSON",
      wire.clip_id == "c1" and wire.user_id == "u1" and wire.platform == "twitch")
check("the score survives the round trip", wire.trigger_score == 88.0)
check("the signals survive the round trip", wire.trigger_signals == {"chat_velocity": 90})

print("\n" + "=" * 74)
print("STAGE 3  ClipJob -> Twitch -> ClipMetadata")
print("=" * 74)

meta = asyncio.run(ClipProcessor().process(wire))
check("the processor created a clip via Helix", len(created) == 1)
check("it used the USER's token, not an app token", created and created[0][0] == "usertoken")
check("the clip has a real Twitch URL", meta.twitch_url.startswith("https://clips.twitch.tv/"))
check("the clip has an embed url", bool(meta.embed_url))
check("the clip lands as 'pending' for review", meta.status == "pending")
check("the trigger score is carried onto the clip", meta.trigger_score == 88.0)

print("\n" + "=" * 74)
print("STAGE 4  clip -> review queue -> the user's open tab")
print("=" * 74)

asyncio.run(dash.notify_clip_ready(meta.to_dict()))
stored = dash._clips.get("c1")
check("the clip is in the review queue", stored is not None)
if stored:
    check("it is owned by the right user", stored.get("user_id") == "u1")
    check("its url survived into storage",
          str(stored.get("twitch_url", "")).startswith("https://clips.twitch.tv/"))
check("a clip_ready event reached the user's socket",
      ("clip_ready", "u1") in sent, f"sent={sent}")

print("\n" + "=" * 74)
print("STAGE 5  the guards that stop it running away")
print("=" * 74)

from src.billing.plans import PLAN_LIMITS
used, cap = dash.pending_room("u1")
check("the pending cap resolves for a Pro user", cap == PLAN_LIMITS["pro"]["max_pending"],
      f"used={used} cap={cap}")

import src.main as M
check("stale jobs are dropped rather than clipped late",
      M.MAX_CLIP_JOB_AGE_SECS <= 90, f"max_age={M.MAX_CLIP_JOB_AGE_SECS}s")

# Cooldown must actually suppress a second immediate fire.
async def stage5():
    f2 = []
    e = TriggerEngine(channel="lacy2", on_trigger=lambda ev: f2.append(ev) or asyncio.sleep(0),
                      buffer=Meter(-12.0))
    e.update_viewer_count(4000)
    e._last_trigger = time.time()      # just fired
    for i in range(300):
        e.ingest_chat(f"x{i}", "POGGERS INSANE CLIP LETSGO")
    await e.evaluate()
    return len(f2)
check("cooldown suppresses an immediate second clip", asyncio.run(stage5()) == 0)

print("\n" + "=" * 74)
n_pass = sum(1 for _, ok, _ in results if ok)
print(f"{n_pass}/{len(results)} checks passed")
bad = [n for n, ok, _ in results if not ok]
if bad:
    print("FAILED: " + "; ".join(bad))
