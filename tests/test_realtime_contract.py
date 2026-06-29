"""
Realtime-contract regression test (CLAUDE.md rule #2).

Every event string the backend broadcasts must have a matching branch in the
frontend ws.onmessage handler (directly, or via the in-page hz_ws channel the
VOD/Settings screens listen on). An emitted event with no handler is silently
dropped — the same as not sending it. This test greps both sides and asserts the
emitted set is a subset of the handled set, so adding a broadcast without a
handler fails CI.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ["src/dashboard/api.py", "src/main.py", "src/ingestion/stream_worker.py"]
FRONTEND = "src/dashboard/aurora_html.py"

# Events the backend emits but the frontend deliberately doesn't toast/handle
# (no user-visible effect needed). Keep this list tiny and justified.
INTENTIONALLY_UNHANDLED = set()


def _emitted_events() -> set[str]:
    events = set()
    for rel in BACKEND:
        text = (ROOT / rel).read_text()
        events |= set(re.findall(r'"event":\s*"(\w+)"', text))
    return events


def _handled_events() -> set[str]:
    text = (ROOT / FRONTEND).read_text()
    handled = set(re.findall(r"(?:msg|m)\.event==='(\w+)'", text))
    # `['a','b'].includes(msg.event)` / `(m.event)` array forms
    for arr in re.findall(r"\[([^\]]*)\]\.includes\((?:msg|m)\.event\)", text):
        handled |= set(re.findall(r"'(\w+)'", arr))
    return handled


def test_every_emitted_event_has_a_frontend_handler():
    emitted = _emitted_events()
    handled = _handled_events()
    missing = emitted - handled - INTENTIONALLY_UNHANDLED
    assert not missing, (
        f"Backend emits these events with no frontend ws.onmessage handler "
        f"(silently dropped): {sorted(missing)}"
    )


def test_clip_failed_and_stream_error_are_handled():
    # The two gaps this batch fixed — guard them explicitly so they can't regress.
    handled = _handled_events()
    assert "clip_failed" in handled
    assert "stream_error" in handled


def test_screen_local_data_self_heals_on_reconnect():
    # refetchAll() must fan out an 'hz_refetch' event, and the screen-local data
    # sources (VOD jobs, Settings stats) must listen for it — otherwise they go
    # stale after a WS reconnect/deploy (CLAUDE.md rule #3).
    text = (ROOT / FRONTEND).read_text()
    assert "dispatchEvent(new CustomEvent('hz_refetch'))" in text
    # At least two screens (VodScreen + SettingsScreen) re-pull on it.
    assert text.count("addEventListener('hz_refetch'") >= 2
