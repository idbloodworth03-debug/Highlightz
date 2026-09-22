# Highlightz

## This branch is NOT connected to production — read this before trusting anything else in this file's history

An earlier version of this file (first commit on `claude/exciting-mendel-i3zemi`)
told future sessions to always give a `git checkout -B <branch> origin/<branch>`
deploy command against `/opt/highlightz` for whatever branch happened to be
checked out here. That's wrong and it's what caused a real production
incident on 2026-09-22: this branch forks off a stale, 2-commit `main` with
an entirely different (and much older) codebase than what actually runs in
production, and that command force-reset prod onto it.

**The real rules live on `claude/handoff-context-n4badl`** — read `CLAUDE.md`
and `docs/HANDOFF.md` there in full before doing any further work on this
project, regardless of which branch you were told to develop on. Key facts
from that file, so they don't get missed:

- Production deploys from exactly ONE branch: `claude/handoff-context-n4badl`.
  Nothing else, ever. The sanctioned deploy command is:
  `cd /opt/highlightz && git fetch origin && git reset --hard origin/claude/handoff-context-n4badl && systemctl restart highlightz`
- **Never give a deploy command, and never deploy, without the user
  explicitly confirming first.** Don't volunteer one after a push.
- The real dashboard frontend is a React app (Babel-standalone) embedded in
  `aurora_html.py`, not the plain HTML in this repo's `api.py`
  (`DASHBOARD_HTML`) — that file is from the old, abandoned architecture and
  is not what production serves.
- `main` in this repo is stale and not authoritative for anything.

Do not restate or duplicate the real CLAUDE.md/HANDOFF.md content here —
read it fresh from `claude/handoff-context-n4badl` each time, since it
changes frequently and a stale copy is worse than no copy.
