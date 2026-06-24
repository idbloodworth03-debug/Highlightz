# Highlightz — engineering conventions

## Realtime is mandatory: the UI must never need a manual refresh

The dashboard is a long-lived single-page app. Users keep a tab open for hours
while streams run. **Any change to state a user can see must reach their open tab
live, over the WebSocket — never on the next page load.** "Refresh to see it" is a
bug, not an acceptable fallback.

This is a hard rule for *every* change going forward. When you add or modify a
feature, you are not done until the realtime path is wired end-to-end.

### The contract — satisfy all four for any user-visible change

1. **Backend mutation → broadcast.** Any endpoint, worker, or background task that
   changes user-visible state (clips, streams, stream status, profiles, account /
   subscription, settings, admin actions on a user) must call `broadcast(event,
   user_id=...)` after the mutation. Scope it: pass the affected `user_id` so the
   event only reaches that user's sockets; pass `user_id=None` only for genuinely
   global changes. Canonical machinery lives in `src/dashboard/api.py`
   (`broadcast`, `notify_clip_ready`).

2. **New event → frontend handler.** Every event string the backend can emit must
   have a matching branch in the `ws.onmessage` handler in
   `src/dashboard/aurora_html.py`. An event with no handler is silently dropped —
   that is the same as not sending it. Grep both sides when adding an event:
   the set of emitted `"event"` names and the set of handled names must match.

3. **Fetch-on-mount data → also in `refetchAll()`.** Any state a screen loads once
   via `fetch(...)` on mount must also be pulled by `refetchAll()` in
   `aurora_html.py`, OR kept current by a WS event it subscribes to. `refetchAll()`
   runs on mount and on every WebSocket reconnect — that is what makes the app
   self-heal after a disconnect (laptop sleep, network blip, **server restart on
   deploy**). If you add a new top-level data source and don't put it there, it
   goes stale after the next reconnect.

4. **Don't break reconnect resync.** The socket reconnects automatically and
   `ws.onopen` calls `refetchAll()` on every open after the first. Keep that intact.
   Per-screen sockets/listeners (e.g. the `hz_ws` in-page CustomEvent channel used
   by VOD and Settings stats) must also tolerate reconnects.

### Checklist before calling a change done

- [ ] Did I mutate anything a user sees? → it broadcasts an event, scoped by `user_id`.
- [ ] Did I add an event name? → there is a handler for it in `ws.onmessage`.
- [ ] Did I add a new fetched-on-mount data source? → it's in `refetchAll()` or driven by a WS event.
- [ ] Would the feature still be correct if the socket dropped and reconnected mid-use? (It must be.)
- [ ] No code path tells the user to "refresh"; nothing relies on the next page load.

### Worked example (the realtime wiring that already exists)

Admin revoke is the reference pattern: the endpoint stops the user's streams,
calls `broadcast({"event": "subscription_expired", ...}, user_id=uid)`, and the
frontend's `subscription_expired` branch calls `refetchAll()` + shows a toast — so
a revoked user's tab updates instantly with no refresh. Mirror this shape for new
mutations.

## Audits are read-only until a finding is verified on the real target

Investigating ("audit", "make sure X works", "why is Y broken") is **separate
from changing code**. An audit produces a *diagnosis*, not a commit. Do not bundle
speculative fixes into an audit. The discipline:

1. **Verify the environment fact on the machine it applies to.** Production runs at
   `/opt/highlightz` on a separate box; the dev container at `/home/user/Highlightz`
   is **not** production. Never conclude "binary X is missing", "env var Y is unset",
   or "the service is down" from the dev container — that says nothing about prod.
   If a finding depends on the runtime environment, confirm it on prod (the user can
   run a command) or label it explicitly as an *unverified assumption*.

2. **Prove the regression before fixing it.** If something "worked yesterday", find
   what actually changed (`git log`, the diff) and reason about whether that change
   *can* produce the symptom. A change that only lowers a threshold cannot cause
   *fewer* triggers — don't "fix" it. Match the mechanism to the symptom first.

3. **Check the fix doesn't misfire on the normal path.** Before changing trigger /
   scoring / clip code, ask: what does this condition do on a *healthy* stream?
   Example of the trap: `audio_db <= -100` looks like "audio is dead", but
   `_rms_db()` returns exactly `-100` for genuine silence on a working feed — so the
   "fix" fires on every quiet moment. Trace the sentinel/edge values before relying
   on them.

4. **State blast radius and how it was verified in the commit.** For any trigger/clip
   change: what it touches, why it can't reduce clips on a healthy stream, and how
   you confirmed it (compiled, JSX-parsed, reproduced the bug, checked prod).

When in doubt, hand the user a diagnostic command to run on production and wait for
real output instead of changing code on an assumption.

## Other notes

- Frontend is React via Babel-standalone embedded as a string in
  `aurora_html.py` — **no bundler**. A JS/JSX syntax error white-screens the whole
  app, so validate JSX (e.g. parse it with `@babel/preset-react`) before pushing.
- Deploys restart the server, which drops every open socket at once — rule #3/#4
  above are what keep that from looking like an outage to users with tabs open.
