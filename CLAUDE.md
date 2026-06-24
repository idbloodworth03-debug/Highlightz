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

## Other notes

- Frontend is React via Babel-standalone embedded as a string in
  `aurora_html.py` — **no bundler**. A JS/JSX syntax error white-screens the whole
  app, so validate JSX (e.g. parse it with `@babel/preset-react`) before pushing.
- Deploys restart the server, which drops every open socket at once — rule #3/#4
  above are what keep that from looking like an outage to users with tabs open.
