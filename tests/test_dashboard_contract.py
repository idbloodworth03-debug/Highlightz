"""
Dashboard internal contracts — the ones a JSX syntax check cannot catch.

The app is React via Babel-standalone with no bundler and no type checker, so
a route that is wired in one table but missing from another compiles perfectly
and then throws at runtime. Because the throw happens inside the top-level
RdApp render, React unmounts the whole tree: the user gets a white screen, not
a broken tab.

That is exactly what happened when Clip Upload was added to NAV but not to
HEAD — `HEAD[route][0]` on an undefined entry took down the entire dashboard.
These tests keep every per-route table in step.
"""

import re
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parent.parent / "src/dashboard/aurora_html.py"
SRC = FRONTEND.read_text()


def _nav_routes() -> set[str]:
    nav = re.search(r"const NAV=\[(.*?)\];", SRC, re.S)
    assert nav, "NAV table not found"
    return set(re.findall(r"\{id:'(\w+)'", nav.group(1)))


def _head_routes() -> set[str]:
    head = re.search(r"const HEAD=\{(.*?)\};", SRC, re.S)
    assert head, "HEAD table not found"
    return set(re.findall(r"(\w+):\[", head.group(1)))


def _dispatch_routes() -> set[str]:
    return set(re.findall(r"route==='(\w+)'", SRC))


def test_every_nav_tab_has_a_header_entry():
    """A NAV id with no HEAD entry white-screens the app the moment it is
    clicked, because RdApp reads HEAD[route][0] unguarded."""
    missing = _nav_routes() - _head_routes()
    assert not missing, (
        f"NAV tabs with no HEAD entry — clicking these white-screens the whole "
        f"dashboard: {sorted(missing)}"
    )


def test_every_nav_tab_renders_a_screen():
    """A NAV id with no branch in the route dispatch silently falls through to
    the final `else` (Settings), so the tab appears to do nothing."""
    # 'settings' is the dispatch's terminal else-branch, so it never appears as
    # an explicit route=== comparison.
    missing = _nav_routes() - _dispatch_routes() - {"settings"}
    assert not missing, f"NAV tabs with no screen in the route dispatch: {sorted(missing)}"


def test_no_header_entries_for_routes_that_do_not_exist():
    """The reverse drift: a HEAD entry left behind after a tab is removed is
    dead weight that misleads the next person editing these tables."""
    stale = _head_routes() - _nav_routes()
    assert not stale, f"HEAD entries with no NAV tab: {sorted(stale)}"


@pytest.mark.parametrize("route", sorted(_nav_routes()))
def test_route_is_wired_into_every_table(route):
    """Per-route so a failure names the offending tab directly."""
    assert route in _head_routes(), f"{route!r} missing from HEAD"
    assert route in _dispatch_routes() or route == "settings", \
        f"{route!r} missing from the route dispatch"


def _kick_blocked() -> set[str]:
    m = re.search(r"const KICK_BLOCKED=\[(.*?)\];", SRC, re.S)
    assert m, "KICK_BLOCKED table not found"
    return set(re.findall(r"'(\w+)'", m.group(1)))


def test_kick_blocked_tabs_are_real_nav_tabs():
    """A typo'd id here would silently block nothing."""
    unknown = _kick_blocked() - _nav_routes()
    assert not unknown, f"KICK_BLOCKED lists ids that are not NAV tabs: {sorted(unknown)}"


def test_kick_blocked_nav_buttons_are_actually_disabled_not_just_dimmed():
    """Greying a button out without disabling it leaves it clickable — the user
    lands on a dead tab and the app looks broken rather than closed.

    The click handler must also bail, because `disabled` alone does not stop a
    programmatic or keyboard-triggered click in every browser.
    """
    nav = re.search(r"NAV\.filter\(.*?\}\)\}", SRC, re.S)
    assert nav, "nav render block not found"
    block = nav.group(0)
    assert "const blocked = activePlatform==='kick' && KICK_BLOCKED.includes(n.id)" in block
    # The lookbehind matters: `aria-disabled={blocked}` contains the literal
    # `disabled={blocked}`, so a plain substring check passes even when the
    # real `disabled` attribute has been removed and the button is clickable
    # again. Only the standalone attribute actually blocks the click.
    assert re.search(r"(?<![-\w])disabled=\{blocked\}", block), \
        "blocked tab is styled/aria-marked but still clickable"
    assert "if(blocked) return;" in block, "click handler does not bail on a blocked tab"


def test_kick_never_traps_the_user():
    """Everything platform-specific is closed on Kick, so the ways OUT must
    stay open or the only escape is a page refresh."""
    blocked = _kick_blocked()
    for escape in ("account", "feedback"):
        assert escape not in blocked, f"{escape} must stay reachable from Kick"
    # The platform switch and sign-out live outside the NAV loop entirely, so
    # KICK_BLOCKED cannot reach them; assert they are still rendered.
    assert "switchPlatform('twitch')" in SRC
    assert "/logout" in SRC


def test_held_back_features_default_to_closed_while_me_is_loading():
    """`me` is null on first paint. If the flag defaulted open, the real screen
    would flash before /me arrives and then get yanked away."""
    m = re.search(r"const uploadsOn = ([^;]+);", SRC)
    assert m, "uploadsOn not found"
    expr = m.group(1)
    assert "me &&" in expr, "uploadsOn must be false until /me has loaded"
    assert "features?.uploads" in expr and "is_admin" in expr


def test_a_failed_clip_import_leaves_the_retry_button_in_place():
    """`started` gates the whole import UI: false shows the load button, true
    shows the results view.

    It was originally set in a `finally`, so a FAILED first load flipped it
    true — the retry button vanished and the user was left with an error
    message sitting next to "No clips on your channel yet". Two contradictory
    statements and no way forward. It must only be set on the success path.
    """
    m = re.search(r"const fetchPage = useCallback\(async \(cur\) => \{(.*?)\n  \}, \[\]\);",
                  SRC, re.S)
    assert m, "fetchPage not found"
    body = m.group(1)
    fin = re.search(r"finally \{([^}]*)\}", body)
    assert fin, "no finally block in fetchPage"
    assert "setStart" not in fin.group(1), \
        "setStart in finally — a failed load hides the retry button"
    assert "setStart(true)" in body, "success path never marks the load complete"


def _raw_babel_block() -> str:
    """The React source AS TYPED in the file (escapes not yet processed)."""
    m = re.search(r'<script type="text/babel">(.*?)</script>', SRC, re.S)
    assert m, "babel block not found"
    return m.group(1)


# Escapes Python CONSUMES inside a normal triple-quoted string. Anything here
# written with a single backslash silently changes before the browser sees it.
_PY_EATS = "nrtbfav0x'\"" + '"'


def test_no_python_escape_is_left_for_python_to_eat_in_the_js():
    """The whole React app sits inside a Python triple-quoted string, so PYTHON
    processes escapes before the browser ever loads the file.

    `split('\\n')` typed with ONE backslash becomes a real newline in the
    parsed string, which terminates the JS string literal and white-screens the
    entire dashboard. Worse, it survives a syntax check that reads the raw file
    — there the escape still looks intact. That is exactly how it happened
    (ClipEditor's caption split, 2026-07-31).

    Rule: inside the babel block, any backslash meant for JavaScript must be
    DOUBLED. `\\n` in the file gives JS its `\n`; a lone `\n` gives it a
    line break and a syntax error.
    """
    offenders = []
    for n, line in enumerate(_raw_babel_block().splitlines(), 1):
        # A backslash not itself escaped, followed by something Python acts on.
        for m in re.finditer(r"(?<!\\)\\([" + re.escape(_PY_EATS) + r"])", line):
            offenders.append(f"line {n}: \\{m.group(1)} -> {line.strip()[:78]}")
    assert not offenders, (
        "single-backslash escape inside the JS — Python will consume it before "
        "the browser sees it; double it:\n  " + "\n  ".join(offenders[:10])
    )


def test_twitch_clips_play_in_a_lightbox_not_inside_the_grid_card():
    """A Twitch embed draws its own title, avatar and controls OVER the video.
    Squeezed into a ~300px grid cell those overlap the picture and the card
    reads as broken (reported from prod, 2026-07-31). The player needs real
    width, so playback belongs in the lightbox.
    """
    card = re.search(r'<div className="rd-tw" key=\{c\.id\}>(.*?)</div>\s*\)\)\}', SRC, re.S)
    assert card, "clip card markup not found"
    assert "<iframe" not in card.group(1), "embed is back inside the grid card"
    # The lightbox lives after the card's closing brace, so it is matched
    # against the whole file rather than a function-body slice.
    # Matched loosely: the element carries "tw-box glass", so an exact
    # className== comparison would fail on a purely cosmetic class change.
    assert "tw-box" in SRC, "no lightbox to play in"
    assert re.search(r'className="tw-frame">\s*<iframe', SRC), \
        "lightbox has no embed — clicking a clip would open an empty box"


def test_uploading_opens_the_editor_without_a_second_click():
    """Uploading here exists to enable editing, so making the user hunt for an
    Edit button afterwards is pure friction. Only the FIRST of a batch opens —
    dropping five files must not fight the user for the screen."""
    m = re.search(r"const sendOne = \(file\) => new Promise\(resolve=>\{(.*?)\n  \}\);", SRC, re.S)
    assert m, "sendOne not found"
    body = m.group(1)
    assert "setEditing(prev => prev || up)" in body, (
        "upload does not open the editor, or would clobber an already-open one"
    )


def test_the_dropzone_says_it_is_the_way_into_the_editor():
    """A user landing here needs to know the editor exists and how to reach it.
    "Drop clips here" alone describes an upload box, not a way in."""
    m = re.search(r'className=\{\'rd-drop\'.*?</div>\n\s*<input ref=\{fileRef\}', SRC, re.S)
    assert m, "dropzone block not found"
    assert re.search(r'class[Nn]ame="dt">Drop a clip here to open the editor', m.group(0)), \
        "dropzone no longer tells the user it opens the editor"


def test_already_uploaded_clips_are_one_click_from_the_editor():
    """Without this the only visible route in is 'upload something', which is a
    dead end for a user who already has clips here and wants to re-cut one."""
    assert 'className="rd-picks"' in SRC, "no quick-pick row for existing clips"
    picks = re.search(r'className="rd-picks">(.*?)</div>', SRC, re.S)
    assert picks, "quick-pick row markup not found"
    assert "setEditing(u)" in picks.group(1), \
        "quick-pick chips do not open the editor"
    assert "uploads.length>0 &&" in SRC, \
        "quick-pick row must be hidden when there is nothing to pick"


def _how_steps(component: str) -> list[tuple[str, str]]:
    """The numbered strip inside one component. Scoped by component because
    every tab has one now — an unscoped search silently returns whichever is
    defined first in the file."""
    body = SRC[SRC.index("function " + component + "("):]
    m = re.search(r'className="rd-how">(.*?)</div>\n\s*\)\)\}', body, re.S)
    assert m, f"how-it-works strip not found in {component}"
    return re.findall(r"'(?:\w+)','(\d)','([^']+)'", m.group(1))


def test_the_editor_tab_explains_the_flow_before_asking_for_a_file():
    """A dropzone alone doesn't tell anyone an editor exists, what it does, or
    where the result goes. Three numbered steps state it once, up top."""
    steps = _how_steps("UploadScreen")
    assert [n for n, _ in steps] == ["1", "2", "3"], f"steps not 1-2-3: {steps}"
    titles = " ".join(t.lower() for _, t in steps)
    assert "add" in titles and "edit" in titles and "export" in titles, \
        f"steps don't cover add/edit/export: {titles}"


def test_the_scheduler_tab_explains_itself_the_same_way():
    """It is a new tab with clips already in it that the user did not put
    there. Without the strip, "why is this here and what do I do" has no
    answer on screen."""
    steps = _how_steps("ScheduleScreen")
    assert [n for n, _ in steps] == ["1", "2", "3"], f"steps not 1-2-3: {steps}"
    titles = " ".join(t.lower() for _, t in steps)
    assert "export" in titles, "never says where these clips come from"
    assert "post" in titles, "never says what to do with them"


def test_the_twitch_list_says_why_those_clips_cannot_be_edited():
    """Two lists sit on this screen and only one has Edit buttons. Left
    unexplained that reads as a bug rather than a Twitch limitation."""
    m = re.search(r"function TwitchImport\(\) \{(.*?)className=\"rd-drop\"", SRC, re.S)
    block = m.group(1) if m else SRC
    assert re.search(r"Twitch doesn't let apps download clip files", block), \
        "no explanation for why Twitch clips have no Edit button"


def test_adding_a_stream_lives_on_live_streams_not_clip_review():
    """The two tabs had one job each and were sharing a screen: Clip Review
    carried the add-stream box, so neither tab's name described what it did.

    Review is now purely for judging clips; adding and monitoring channels is
    Live Streams. The add panel must exist in exactly one place, or the split
    is cosmetic.
    """
    review = SRC[SRC.index("function ReviewScreen({"):SRC.index("function StreamsScreen({")]
    for gone in ("Add a stream", "Monitor stream", "streams/suggest", "Monitored streams"):
        assert gone not in review, f"Clip Review still owns the add-stream UI ({gone!r})"
    assert "onAdd" not in review, "Clip Review can still add streams"

    streams = SRC[SRC.index("function StreamsScreen({"):SRC.index("function LibraryScreen({")]
    assert "AddStreamPanel" in streams, "Live Streams has no add-stream panel"


def test_live_streams_can_still_add_the_very_first_channel():
    """StreamsScreen used to early-return a bare 'No streams monitored' when
    the list was empty. Now that it owns the only add box, that return has to
    render the panel too — otherwise a brand-new user has nowhere to start."""
    streams = SRC[SRC.index("function StreamsScreen({"):SRC.index("function LibraryScreen({")]
    early = re.search(r"if\(!active\) return \((.*?)\n  \);", streams, re.S)
    assert early, "empty-state early return not found"
    assert "AddStreamPanel" in early.group(1), \
        "empty Live Streams has no way to add a channel — new users are stuck"


def test_clip_review_uses_the_full_width_now_that_the_rail_is_gone():
    review = SRC[SRC.index("function ReviewScreen({"):SRC.index("function StreamsScreen({")]
    assert "rd-body-full" in review, "review grid still reserves space for a removed rail"
    assert ".rd-body-full{grid-template-columns:1fr}" in SRC, "rd-body-full has no rule"


def test_the_captions_panel_is_hidden_when_the_feature_is_switched_off():
    """CAPTIONS_ENABLED was unset on prod while UPLOADS_ENABLED was true, so
    every Pro user saw a 'Generate captions' button that 503'd on every click.
    A visible control that always fails is the same mistake as a greyed-but-
    clickable Kick tab: the user cannot tell a broken app from a closed door."""
    assert "captionsOn && <div className=\"ed-grp\">" in SRC, \
        "Auto-captions panel is rendered unconditionally again"
    assert "const captionsOn = !!(me && (me.features?.captions || me.is_admin));" in SRC, \
        "captionsOn must follow the same release-flag + admin-bypass shape as uploadsOn"
    # It has to actually reach the editor, not just be computed.
    # NOTE [^>]* cannot be used here: the call site contains an arrow function
    # (`()=>setEditing(null)`) and the > in => ends the class early.
    assert re.search(r"<ClipEditor\b[^\n]*captionsOn=\{captionsOn\}", SRC), \
        "captionsOn computed but never passed to ClipEditor"
    assert re.search(r"<UploadScreen\b[^\n]*captionsOn=\{captionsOn\}", SRC), \
        "captionsOn never reaches UploadScreen"


def test_the_editor_resyncs_caption_state_on_reconnect():
    """A deploy kills the transcription task AND the in-memory job record, so
    captions_ready is never sent — nobody is left to send it. Without an
    hz_refetch listener the panel sat on 'Transcribing... 40%' forever and only
    a manual page refresh cleared it, which the realtime rule forbids."""
    editor = SRC[SRC.index("function ClipEditor("):SRC.index("function UploadScreen(")]
    # Assert the SUBSCRIPTION, not the word: "hz_refetch" also appears in the
    # cleanup line, so a substring check passed with the listener deleted.
    assert "window.addEventListener('hz_refetch', load)" in editor, \
        "ClipEditor never re-pulls caption state on reconnect"
    assert "window.removeEventListener('hz_refetch', load)" in editor, \
        "listener added but never cleaned up — leaks one per editor open"
    assert "the server restarted" in editor, \
        "a job the server has forgotten must be cleared with a reason, not left spinning"
    # The optimistic setCapJob happens before the POST lands; a reconnect in
    # that window must not cancel a job that is about to exist.
    assert "startedAt" in editor and "6000" in editor, \
        "no grace window — a reconnect racing the POST would kill a live job"


def test_the_queue_never_claims_it_will_post_for_you():
    """The app holds no TikTok/Instagram/YouTube credentials, so the queue can
    only remind. If the UI implies automation someone misses a posting slot
    they were counting on — worse than not shipping the feature."""
    low = SRC.lower().replace("’", "'")
    for phrase in ("never posts for you",
                   "never asks for your tiktok, instagram\n        or youtube login",
                   "you post it from\n        your own account",
                   "a reminder here is a nudge, not an upload"):
        assert phrase in low, f"the Scheduler no longer says {phrase!r}"
    for lie in ("we'll post it", "posts automatically", "auto-post",
                "connect your tiktok", "link your instagram"):
        assert lie not in low, f"UI claims {lie!r} — it cannot"


def test_queue_times_cross_the_wire_as_epoch_seconds():
    """datetime-local has no timezone. Sending the raw string would make the
    server guess which 19:00 was meant; a user who travels gets posts due at
    the wrong hour with no way to tell."""
    assert "new Date(e.target.value).getTime()/1000" in SRC, \
        "local wall-clock time is being sent without being resolved to an instant"
    # And back the other way: filling the input from stored epoch seconds must
    # use local getters. toISOString() would hand the input UTC and shift every
    # displayed time by the user's offset.
    assert "function toLocalInput(" in SRC
    body = SRC[SRC.index("function toLocalInput("):]
    body = body[:body.index("function qWhen(")]
    assert "toISOString" not in body, "datetime-local filled with UTC"
    assert "getHours()" in body, "not using local time to fill the input"
