"""Which posting platforms are open to ordinary accounts on this server? Read-only.

    cd /opt/highlightz && venv/bin/python scripts/posting_check.py

Owner, 2026-11-19: "make sure instagram is off for users since it is not
complete". Whether it is off is decided by one setting on the server
(PUBLIC_PLATFORMS in .env; src/publish/release.py), and by which accounts are
marked as early-access testers. Neither can be seen from the code. This
reads them.

WHAT IT SAYS
  1. The switch as the site reads it: which platforms an ordinary account may
     connect and post to, and which are held as "coming soon".
  2. Whether the site that is RUNNING has the rule that the poster never
     posts to a platform that is not open to the account (added the same
     day; before it, an account that already held a connection kept being
     posted to).
  3. Who can reach a held platform anyway, by design: staff, and accounts
     marked early access (how app reviewers and testers get in).
  4. Ordinary accounts that HOLD a connection to a held platform, by name,
     and how many queued posts name one. With the rule running, none of
     those is posted.
  5. WHAT HAPPENED TO THE POSTS THAT WERE TRIED (owner, the same day: "it
     looks like the post button in general is not working"). Every attempt
     leaves its outcome on the queue item: posted, or failed with the
     platform's own reason. So: how many went out and how many failed, by
     platform, in the last day and week; the commonest reasons; the last
     attempts in order; how many connected accounts have to reconnect
     before they can post at all; and what the poster logged.

Read-only. Prints platform names, counts, and an account's NAME only when it
holds a connection to a platform that is not open to it. Never a token, an
address or a setting other than the one this is about.
"""
import collections
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEPLOY = ("cd /opt/highlightz && git fetch origin && git reset --hard "
          "origin/claude/handoff-context-n4badl && systemctl restart highlightz")
LABELS = {"youtube": "YouTube", "tiktok": "TikTok", "instagram": "Instagram", "x": "X"}


def _names(ids) -> str:
    ids = [LABELS.get(p, p) for p in ids]
    return ", ".join(ids) if ids else "none"


def poster_has_the_rule(root: pathlib.Path = ROOT) -> bool:
    try:
        return "is_open(p)" in (root / "src" / "publish" / "poster.py").read_text()
    except OSError:
        return False


def poster_changed_at(root: pathlib.Path = ROOT) -> float:
    try:
        return (root / "src" / "publish" / "poster.py").stat().st_mtime
    except OSError:
        return 0.0


POST_EVENTS = ("post_published", "post_failed", "tiktok_user_info_failed", "tiktok_user_info_refused",
               "tiktok_creator_names_failed", "poster_account_lookup_failed", "poster_notify_failed")


def clean(text) -> str:
    """A platform's reason, safe to paste: anything shaped like a key or a
    token is taken out, and it is cut short."""
    t = re.sub(r"[A-Za-z0-9_\-]{24,}", "[...]", " ".join(str(text or "").split()))
    return t[:110] + ("..." if len(t) > 110 else "")


def attempts(items, conns, users, now: float) -> dict:
    """What the queue items and the connections say happened."""
    known = {str(u["id"]): u for u in users if isinstance(u, dict) and u.get("id")}

    def kind(uid: str) -> str:
        u = known.get(uid) or {}
        return "staff" if u.get("is_admin") else "early access" if u.get("early_access") else "ordinary"
    rows = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        for p, r in (it.get("results") or {}).items():
            if not isinstance(r, dict) or r.get("status") not in ("posted", "failed"):
                continue
            try:
                at = float(r.get("at") or 0)
            except (TypeError, ValueError):
                at = 0.0
            rows.append({"at": at, "platform": str(p), "ok": r["status"] == "posted",
                         "error": clean(r.get("error")), "who": kind(str(it.get("user_id") or ""))})
    rows.sort(key=lambda r: -r["at"])
    counts: dict = {}
    for label, span in (("day", 86400.0), ("week", 7 * 86400.0)):
        c: dict = collections.defaultdict(lambda: [0, 0])                 # platform -> [posted, failed]
        for r in rows:
            if now - r["at"] <= span:
                c[r["platform"]][0 if r["ok"] else 1] += 1
        counts[label] = {k: tuple(v) for k, v in c.items()}
    why: dict = collections.defaultdict(collections.Counter)
    for r in rows:
        if not r["ok"] and now - r["at"] <= 7 * 86400:
            why[r["platform"]][r["error"] or "(no reason recorded)"] += 1
    broken: dict = collections.defaultdict(collections.Counter)
    healthy: collections.Counter = collections.Counter()
    for c in conns if isinstance(conns, list) else []:
        if isinstance(c, dict) and c.get("platform") and str(c.get("user_id") or "") in known:
            if c.get("last_error"):
                broken[str(c["platform"])][clean(c["last_error"])] += 1
            else:
                healthy[str(c["platform"])] += 1
    return {"last": rows[:12], "counts": counts, "why": {k: v.most_common(4) for k, v in why.items()},
            "broken": {k: v.most_common(3) for k, v in broken.items()},
            "broken_n": {k: sum(v.values()) for k, v in broken.items()}, "healthy": dict(healthy),
            "ever": len(rows)}


def logged(since_s: float, unit: str = "highlightz", cap_s: float = 6 * 3600) -> tuple[collections.Counter, bool, float]:
    """(how often the poster logged each of POST_EVENTS, could the journal be
    read, how far back). Names only."""
    span = max(60.0, min(float(since_s), cap_s))
    got: collections.Counter = collections.Counter()
    try:
        proc = subprocess.Popen(["journalctl", "-u", unit, "--since", f"-{int(span)}s", "-o", "cat", "--no-pager"],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, errors="replace")
    except FileNotFoundError:
        return got, False, span
    any_line = False
    try:
        for raw in proc.stdout:
            any_line = True
            if "post" not in raw and "tiktok" not in raw:
                continue
            for name in POST_EVENTS:
                if f'"{name}"' in raw:
                    got[name] += 1
                    break
    finally:
        proc.stdout.close()
        code = proc.wait()
    return got, bool(code == 0 and any_line), span


def _when(seconds: float) -> str:
    seconds = max(0.0, seconds)
    return (f"{seconds / 60:.0f} min ago" if seconds < 5400 else
            f"{seconds / 3600:.1f} h ago" if seconds < 2 * 86400 else f"{seconds / 86400:.0f} days ago")


def posts_report(tried: dict, now: float, events=None, journal: bool = False, span: float = 0.0) -> list[str]:
    out = ["", "=" * 70, "WHAT HAPPENED TO THE POSTS THAT WERE TRIED", "=" * 70]
    if not tried["ever"]:
        out.append("  no queue item records an attempt: nothing has been posted or has failed")
    for label, title in (("day", "last 24 hours"), ("week", "last 7 days")):
        c = tried["counts"][label]
        if not c:
            out.append(f"  {title:<14}: nothing tried")
            continue
        out.append(f"  {title:<14}: " + ";  ".join(
            f"{LABELS.get(p, p)} {ok} posted, {bad} failed" for p, (ok, bad) in sorted(c.items())))
    for p, reasons in sorted(tried["why"].items()):
        out.append(f"  why {LABELS.get(p, p)} failed (last 7 days):")
        for text, n in reasons:
            out.append(f"    {n:>4} x  {text}")
    out.append("  connected accounts that can post / must reconnect first:")
    for p in ("youtube", "tiktok", "instagram", "x"):
        ok, bad = tried["healthy"].get(p, 0), tried["broken_n"].get(p, 0)
        if ok or bad:
            out.append(f"    {LABELS[p]:<30} : {ok:,} / {bad:,}")
            for text, n in tried["broken"].get(p, []):
                out.append(f"        {n:>3} x  {text}")
    if tried["last"]:
        out.append("  the last attempts, newest first:")
        for r in tried["last"]:
            out.append(f"    {_when(now - r['at']):<13} {LABELS.get(r['platform'], r['platform']):<10} "
                       + ("posted" if r["ok"] else "FAILED: " + (r["error"] or "(no reason recorded)"))
                       + f"   ({r['who']} account)")
    if events is not None:
        out.append(f"  what the poster logged in the last {_when(span).replace(' ago', '')}:")
        if not journal:
            out.append("    the journal could not be read from here (run this as root on the server)")
        elif not events:
            out.append("    nothing: no post went out and none failed in that time")
        else:
            for name in POST_EVENTS:
                if events.get(name):
                    out.append(f"    {events[name]:>6,}  {name}")
    return out


def survey(users, conns, items, open_ids) -> dict:
    """What the accounts hold, against what is open to ordinary accounts.
    `open_ids`: the platforms PUBLIC_PLATFORMS opens."""
    known = {str(u["id"]): u for u in users if isinstance(u, dict) and u.get("id")}
    staff = {uid for uid, u in known.items() if u.get("is_admin")}
    early = {uid for uid, u in known.items() if u.get("early_access") and uid not in staff}
    through = staff | early
    by_platform: dict = collections.defaultdict(lambda: [0, 0])      # platform -> [all, ordinary]
    holding: dict = collections.defaultdict(list)                      # held platform -> ordinary names
    for c in conns if isinstance(conns, list) else []:
        if not isinstance(c, dict) or not c.get("platform"):
            continue
        uid, p = str(c.get("user_id") or ""), str(c["platform"])
        if uid not in known:
            continue                                  # a deleted account's leftover
        by_platform[p][0] += 1
        if uid not in through:
            by_platform[p][1] += 1
            if p not in open_ids:
                holding[p].append(str(known[uid].get("username") or "?")[:40])
    queued: collections.Counter = collections.Counter()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict) or str(it.get("user_id") or "") in through:
            continue
        if str(it.get("status") or "") in ("posted", "cancelled", "done"):
            continue
        done = {p for p, r in (it.get("results") or {}).items()
                if isinstance(r, dict) and r.get("status") == "posted"}
        for p in it.get("platforms") or []:
            if p not in open_ids and p not in done:
                queued[str(p)] += 1
    return {"staff": len(staff), "early": len(early), "by_platform": {k: tuple(v) for k, v in by_platform.items()},
            "holding": {k: sorted(v) for k, v in holding.items()}, "queued": dict(queued)}


def report(*, raw: str, open_ids, held_ids, configured: dict, got: dict, now: float,
           started: float | None, changed: float, has_rule: bool, focus: str = "instagram") -> list[str]:
    out = ["=" * 70, "THE SWITCH  (PUBLIC_PLATFORMS, as the site reads it)", "=" * 70]
    out.append(f"  PUBLIC_PLATFORMS                 : {raw or '(empty)'}")
    out.append(f"  open to ordinary accounts        : {_names(open_ids)}")
    out.append(f"  held as \"coming soon\"            : {_names(held_ids)}")
    for p in ("youtube", "tiktok", "instagram", "x"):
        if p in configured:
            out.append(f"  {LABELS[p] + ' app keys':<32} : {'set' if configured[p] else 'not set'}")

    stale = started is not None and changed > started + 5
    out += ["", "=" * 70, "THE SITE THAT IS RUNNING", "=" * 70]
    if started is None:
        out.append("  the site's process               : NOT FOUND")
    else:
        mins = (now - started) / 60
        out.append(f"  started                          : {mins:.0f} min ago" if mins < 90
                   else f"  started                          : {mins / 60:.1f} h ago")
    out.append("  never posts to a held platform   : "
               + ("NOT in the code on disk" if not has_rule else
                  "in the code on disk, NOT in the running site (it was not restarted)" if stale else
                  "yes" if started is not None else "in the code on disk"))

    out += ["", "=" * 70, "WHO CAN REACH A HELD PLATFORM", "=" * 70]
    out.append(f"  staff accounts                   : {got['staff']:,}   (always open to everything configured)")
    out.append(f"  early-access accounts            : {got['early']:,}   (testers and app reviewers: open on purpose)")
    out.append("  connections held, all / by ordinary accounts:")
    for p in ("youtube", "tiktok", "instagram", "x"):
        if p in got["by_platform"]:
            n, ordinary = got["by_platform"][p]
            out.append(f"    {LABELS[p]:<30} : {n:,} / {ordinary:,}" + ("   <- held" if p in held_ids or p not in open_ids else ""))
    for p, names in got["holding"].items():
        out.append(f"  ORDINARY ACCOUNTS HOLDING {LABELS.get(p, p).upper()} ({len(names)}), connected before it was closed:")
        for name in names[:15]:
            out.append(f"    {name}")
        if len(names) > 15:
            out.append(f"    ... and {len(names) - 15} more")
    for p, n in got["queued"].items():
        out.append(f"  queued posts naming {LABELS.get(p, p)} for ordinary accounts: {n:,}")

    out.append("")
    is_open = focus in open_ids
    name = LABELS.get(focus, focus).upper()
    holders = len(got["holding"].get(focus, []))
    if is_open:
        out += [f"  {name} IS OPEN TO EVERY ACCOUNT ON A PLAN THAT POSTS: PUBLIC_PLATFORMS names it.",
                "  Nothing in the code closes it while that setting says so. Send this output",
                "  and you will be given the one command that takes it out."]
    elif not has_rule or stale:
        out += [f"  {name} CANNOT BE CONNECTED OR CHOSEN BY AN ORDINARY ACCOUNT (the switch is off),",
                "  but the running site does not yet have the rule that stops posts to an",
                f"  account that ALREADY holds a connection ({holders} such account(s) above)." if holders else
                "  account that already holds a connection (none does, so nothing can go out).",
                "  Restart it with the deploy command:", "  " + DEPLOY]
    else:
        out += [f"  {name} IS OFF FOR ORDINARY ACCOUNTS: it cannot be connected, chosen or posted to.",
                f"  The {holders} account(s) above still hold an old connection; nothing is posted to it."
                if holders else "  No ordinary account holds a connection to it.",
                f"  Staff and the {got['early']:,} early-access account(s) can still use it, which is how it is tested."]
    return out


def main(argv: list[str]) -> int:
    from config.settings import settings
    from src.publish import providers, release
    store = pathlib.Path(settings.local_storage_path)
    if not store.is_absolute():
        store = ROOT / store

    def load(name, default):
        try:
            return json.loads((store / name).read_text())
        except (OSError, ValueError):
            return default
    users = load("users.json", None)
    if users is None:
        print("The accounts could not be read. Run this as root, in /opt/highlightz.")
        return 1
    open_ids = list(release.public_platforms())
    held_ids = [p for p in release.ORDER if p not in open_ids and release.offered(p)]
    configured = {}
    for p in release.ORDER:
        prov = providers.get(p)
        if prov is not None and (p not in release.OPTIONAL or prov.configured()):
            configured[p] = bool(prov.configured())
    spec = importlib.util.spec_from_file_location("reaper_check", ROOT / "scripts" / "reaper_check.py")
    reaper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reaper)
    conns, items = load("publish_connections.json", []), load("schedule.json", [])
    now, started = time.time(), reaper.site_started()
    got = survey(users, conns, items, set(open_ids))
    lines = report(raw=str(settings.public_platforms or ""), open_ids=open_ids, held_ids=held_ids,
                   configured=configured, got=got, now=now, started=started,
                   changed=poster_changed_at(), has_rule=poster_has_the_rule())
    events, journal, span = logged(now - started if started else 3600.0)
    verdict_at = len(lines) - next(i for i, ln in enumerate(reversed(lines)) if ln == "")
    posts = posts_report(attempts(items, conns, users, now), now, events, journal, span)
    for line in lines[:verdict_at - 1] + posts + lines[verdict_at - 1:]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
