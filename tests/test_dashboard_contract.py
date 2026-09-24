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
    # `view`, not `route`: the dispatch reads a normalised route so a held-back
    # tab cannot be rendered even if something points at it. Matching on
    # `route===` here would find only the two gate conditions and report every
    # other tab as unwired.
    return set(re.findall(r"view==='(\w+)'", SRC))


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
    assert "const blocked = activePlatform==='kick' && !kickOpen && KICK_BLOCKED.includes(n.id)" in block
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
    dead end for a user who already has clips here and wants to re-cut one.
    The route is the card's own Edit button (2026-09-16: the second list of
    chips under the dropzone was clutter, so the grid is the only list)."""
    screen = SRC[SRC.index("function UploadScreen("):SRC.index("function ScanActivity(")]
    grid = re.search(r'className="rd-lib-grid">(.*?)\n\s*</div>\}', screen, re.S)
    assert grid, "clip grid not found"
    assert "onClick={()=>setEditing(u)}" in grid.group(1), \
        "clip cards do not open the editor"
    assert 'className="rd-picks"' not in screen, \
        "the chip list is back: the grid is the one list of clips"


def test_the_walkthrough_is_a_button_not_a_banner():
    """Owner (2026-09-16): the three steps sit behind "How it works", not at
    the top of every visit. The strip must be conditional on that toggle."""
    screen = SRC[SRC.index("function UploadScreen("):SRC.index("function ScanActivity(")]
    assert re.search(r"\{showHow && <div className=\"rd-howbox\">\s*<div className=\"rd-how\">", screen), \
        "walkthrough strip is not behind the showHow toggle"
    assert "setShowHow(v=>!v)" in screen, "no How it works button toggles it"
    assert ">How it works" in screen or "How it works\n" in screen, "button is not labelled How it works"


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
    where the result goes. Three numbered steps state it, behind the
    "How it works" button (see test_the_walkthrough_is_a_button_not_a_banner)."""
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


def test_the_queue_says_exactly_when_it_posts_and_when_it_only_reminds():
    """Until 2026-09-15 this pinned "never posts for you": the app held no
    platform credentials, so a queue that implied automation would cost
    someone a posting slot. The owner reversed that ("I need the scheduler to
    be working and integrated now") and the risk flipped with it: a queue
    that looks MANUAL where it is not posts something the user did not
    expect. So the strings now have to say which platforms are posted to for
    them (connected ones) and which they still post by hand (the rest), and
    the old promise must be gone."""
    low = SRC.lower().replace("’", "'")
    for phrase in ("connected accounts are posted to for you",
                   "the rest get a reminder and one-tap share",
                   "connect an account and highlightz posts your clips to it for you",
                   "only the clips you choose it for, only while it is connected"):
        assert phrase in low, f"the Scheduler no longer says {phrase!r}"
    for gone in ("never posts for you", "a reminder here is a nudge, not an upload",
                 "never asks for your tiktok"):
        assert gone not in low, f"the Scheduler still claims {gone!r} — it posts now"


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


def test_blur_fill_never_draws_an_unblurred_duplicate():
    """The first version needed ctx.filter and fell back to a plain crop
    without it. The backdrop is now a two-pass downscale (1/4 then 1/16)
    drawn back up, which is blurred by construction on every canvas — and
    far cheaper than a filter over a full 720x1280 frame per tick. Where
    ctx.filter exists it only smooths the upscale. Whatever happens, the
    background is never the video drawn sharp behind itself."""
    assert "function blurBackdrop(" in SRC
    bg = SRC[SRC.index("function blurBackdrop("):SRC.index("function capWrap(")]
    assert "Math.round(w / 4)" in bg and "Math.round(w / 16)" in bg, "no two-pass downscale"
    assert "if (ctxCanFilter(ctx)) bc.filter" in bg, "filter is required rather than optional"
    assert "ctx.filter =" not in bg, "a filter on the full-size draw is a full-frame blur per tick"
    body = SRC[SRC.index("function paintFrame("):SRC.index("function ClipEditor(")]
    blur = body[body.index("if (fill === 'blur'"):body.index("} else {")]
    assert "blurBackdrop(ctx, video, w, h, vw, vh" in blur
    assert "ctx.drawImage(video, cx, cy, cw, ch)" in blur, "the foreground is not the contained copy"
    assert "} else {" in body and "Math.max(w / vw, h / vh) * zoom" in body, \
        "no cover path left for the crop fill"


def test_blur_fill_contains_the_video_rather_than_cropping_it():
    """The entire point is that nothing is cut off the sides — if the
    foreground still used cover, blur would be decoration over the same crop."""
    body = SRC[SRC.index("function paintFrame("):]
    body = body[:body.index("function ClipEditor(")]
    blur = body[body.index("if (fill === 'blur'"):body.index("} else {")]
    assert "Math.min(w / vw, h / vh)" in blur, "foreground is not contained"
    assert "Math.max(w / vw, h / vh)" in blur, "background is not over-scaled"


def test_caption_position_and_size_are_driven_by_the_editor_not_hardcoded():
    body = SRC[SRC.index("function drawCaption("):]
    body = body[:body.index("function ClipEditor(")]
    assert "o.capSize" in body and "o.capPos" in body and "o.capHighlight" in body
    assert "const opts = () => ({" in SRC
    o = SRC[SRC.index("const opts = () => ({"):]
    o = o[:o.index("});") + 3]
    for k in ("capSize", "capPos", "capHighlight", "fill"):
        assert k in o, f"{k} never reaches paintFrame, so the control does nothing"


# ── adding several streams in a row ──────────────────────────────────────────
#
# Suggestions are picked on mousedown with preventDefault so the input keeps
# focus. pick() then closed the dropdown — and an already-focused input fires
# no onFocus when clicked, so there was no way to reopen it. Adding a second
# channel meant clicking away and clicking back in. That is the ordinary flow
# for the primary audience: a clipper adding a roster.

def test_picking_a_suggestion_does_not_dead_end_the_dropdown():
    """pick() must not just close the list. Closing it while the caret is still
    in the box is the dead end — nothing can reopen it."""
    # Whole line, not up to the first ';' — the buggy version was a multi-
    # statement body and a [^;]+ capture stopped before the offending call,
    # so the test passed against the exact code it exists to reject.
    m = re.search(r"const pick = \(login\) =>.*", SRC)
    assert m, "pick() not found"
    assert "setSuggOpen(false)" not in m.group(0), (
        "pick() closes the dropdown while the input still holds focus — "
        "the user has to click out and back in to add another channel")


def test_the_list_is_reopened_only_while_the_caret_is_still_in_the_box():
    """Reopening unconditionally would pop an orphaned dropdown after the
    'Monitor stream' button (which does move focus), and would fight a user who
    clicked elsewhere while the add request was still in flight."""
    assert "document.activeElement === inputRef.current" in SRC
    assert "ref={inputRef}" in SRC


def test_the_refresh_waits_for_the_add_to_land():
    """/streams/suggest filters against the channels the server already has, so
    refreshing before the POST returns re-offers the channel just picked and
    the next click 409s."""
    m = re.search(r"const addChannel = async \(login\) => \{(.*?)\n  \};", SRC, re.S)
    assert m, "addChannel() not found"
    body = m.group(1)
    assert "await onAdd(" in body, "the add is not awaited"
    assert body.index("await onAdd(") < body.index("reopenIfFocused()"), \
        "the suggestions are refreshed before the add lands"


def test_repeat_clicks_are_swallowed_while_an_add_is_in_flight():
    """The list stays open during the request, so the row just clicked is still
    under the cursor. Without a guard a double-click posts the same channel
    twice and the second one 409s."""
    m = re.search(r"const addChannel = async \(login\) => \{(.*?)\n  \};", SRC, re.S)
    assert "if (adding" in m.group(1)


def test_clicking_an_already_focused_input_can_reopen_the_list():
    """onFocus does not fire on an already-focused input, so Escape (or any
    other dismissal) would otherwise leave the box permanently inert."""
    assert re.search(r"onClick=\{\(\)=>\{ if\(canSugg && !suggOpen\)", SRC), \
        "no click handler to reopen the suggestions"


# ── clip playback smoothness ─────────────────────────────────────────────────
#
# Moved to tests/test_player_smoothness.py. It lived here as four hand-picked
# selectors, and freezing that list is precisely why the bug came back on every
# screen the list had not thought of. The replacement asserts a SET: every
# selector that turns a blur on must be one the player rule turns off.


# ── the Clip Editor must export SOUND ────────────────────────────────────────
#
# Reported bug: edit a clip, download it, and the file is silent. The cause was
# structural rather than a slip — canvas.captureStream() carries picture only,
# so the recorded stream had a video track and nothing else, and runExport then
# set v.muted = true, which would have silenced the audio even if a track had
# been present. Reproduced in Chromium: the shipped path wrote a file with NO
# audio stream at all; with the clip's audio routed in, the same recording
# carried Opus at -21.4 dB.
#
# These pin the shape of the fix, because the export itself cannot run in
# pytest — there is no browser, no MediaRecorder and no WebAudio here.

def _export_recorder() -> str:
    m = re.search(r"const exportRecorder = async \(\) => \{(.*?)\n  \};", SRC, re.S)
    assert m, "exportRecorder not found"
    return m.group(1)


def _run_export() -> str:
    m = re.search(r"const runExport = async \(\) => \{(.*?)\n  \};", SRC, re.S)
    assert m, "runExport not found"
    return m.group(1)


def test_the_recorded_stream_is_given_an_audio_track():
    """captureStream() on a canvas has no sound. If nothing adds the clip's
    audio to that stream, every exported file is silent — the reported bug.

    Pins the SHAPE, not just the presence of the call: an earlier version of
    this test only looked for `stream.addTrack(` anywhere in the body, and so
    passed happily against `if (false) stream.addTrack(at)` — the exact bug it
    exists to catch.
    """
    body = _export_recorder()
    # 60 for an HD source since 2026-09-15 (Twitch delivers 60 and the
    # platforms take it); the SD floor stays at 30.
    assert "c.captureStream(hd ? 60 : 30)" in body, "export no longer records the canvas"
    # The preview canvas is stage-sized now, so the export must size it to the
    # real output BEFORE captureStream — a resize after would reset the
    # surface and invalidate the capture track.
    assert body.index("c.width = outW; c.height = outH;") < body.index("c.captureStream("), \
        "the canvas is not sized to the output before the capture track is taken"
    assert re.search(
        r"const g = audioGraph\(v, clip\.url\);\s*\n"
        r"\s*if \(g\) \{[\s\S]{0,400}?"
        r"const at = g\.dest\.stream\.getAudioTracks\(\)\[0\];\s*\n"
        r"\s*if \(at\) stream\.addTrack\(at\);", body), \
        "the clip's audio is not added to the recorded stream — exports are silent"


def test_the_audio_track_is_added_before_the_recorder_is_constructed():
    """MediaRecorder latches its track set at construction; a track added
    afterwards is not recorded."""
    body = _export_recorder()
    assert body.index("stream.addTrack(") < body.index("new MediaRecorder("), \
        "audio track added after the recorder was built — it will not be recorded"


def test_export_does_not_mute_the_element_when_it_can_route_the_audio():
    """v.muted silences the CAPTURED track too, so muting to keep the export
    quiet in the room is what would re-break it. The monitor gain is the knob
    that separates the two; v.muted stays only as the no-graph fallback."""
    body = _run_export()
    assert "monitor.gain.value = 0" in body, "no monitor gain — export has no way to be quiet"
    assert re.search(r"if \(g\) g\.monitor\.gain\.value = 0;\s*\n\s*else v\.muted = true;", body), \
        "v.muted is not confined to the branch where no audio graph exists"


def test_whatever_export_changed_it_puts_back():
    """Leaving the monitor at zero would silence PREVIEW playback from then on
    — the user would report the editor going deaf after one export."""
    body = _run_export()
    assert "g.monitor.gain.value = wasGain" in body and "v.muted = wasMuted" in body, \
        "export does not restore what it changed"


def test_a_source_we_cannot_read_falls_back_instead_of_going_silent():
    """createMediaElementSource does not throw on a tainted cross-origin
    source, it yields silence — and the routing it installs is permanent, so
    it would take the PREVIEW's audio with it, which is worse than the bug."""
    m = re.search(r"function canReadAudio\(url\) \{(.*?)\n\}", SRC, re.S)
    assert m, "canReadAudio not found"
    body = m.group(1)
    assert "location.origin" in body and "blob:" in body, \
        "no same-origin guard in front of createMediaElementSource"
    assert "canReadAudio(url)" in re.search(
        r"function audioGraph\(v, url\) \{(.*?)\n\}", SRC, re.S).group(1), \
        "audioGraph does not consult the guard"


def test_the_graph_is_built_once_per_element():
    """createMediaElementSource throws if it is called twice for the same
    element, so a second export would blow up without the cache — and the
    cache must store the null result too, or a failed build retries forever."""
    m = re.search(r"function audioGraph\(v, url\) \{(.*?)\n\}", SRC, re.S)
    body = m.group(1)
    assert "AUDIO_GRAPHS.get(v)" in body and "AUDIO_GRAPHS.set(v," in body
    assert "g !== undefined" in body, \
        "a cached null would be treated as a miss and rebuilt on every export"


def test_every_recorder_type_naming_a_video_codec_also_names_an_audio_one():
    """A type string listing video alone asks some builds for a video-only
    container, which drops the audio track we just went to the trouble of
    adding. Bare container strings are fine — the browser picks both."""
    m = re.search(r"const REC_TYPES = \[(.*?)\];", SRC, re.S)
    assert m, "REC_TYPES not found"
    types = re.findall(r"'([^']+)'", m.group(1))
    assert types, "no recorder types"
    audio = ("mp4a", "opus", "aac", "vorbis")
    for t in types:
        if "codecs=" not in t:
            continue                      # 'video/mp4' — browser chooses both
        assert any(a in t for a in audio), \
            f"{t!r} names a video codec but no audio codec"


def test_the_recorder_is_told_an_audio_bitrate():
    body = _export_recorder()
    assert "audioBitsPerSecond" in body, "no audio bitrate set on the recorder"


# ── the Clip Editor rebuild (2026-09-08) ─────────────────────────────────────
#
# What the rebuild has to keep. The export path is pinned above; these pin the
# editing surface — the parts that made the old editor slow to use, and the
# two rendering traps found while driving the new one in Chromium.

def _editor() -> str:
    return SRC[SRC.index("function ClipEditor("):SRC.index("function UploadScreen(")]


def test_the_preview_paints_on_demand_not_sixty_times_a_second():
    """The old loop repainted a paused 720x1280 canvas every animation frame.
    Now a paused editor paints only when something changed; an export paints
    every tick because captureStream needs a fresh frame to record."""
    ed = _editor()
    assert "dirtyRef" in ed, "no dirty flag — the loop has no way to skip a paint"
    assert re.search(r"if \(dirtyRef\.current \|\| live\)", ed), \
        "the paint is not gated on dirty-or-live"
    assert "const live = L.playing || L.busy || !v.paused;" in ed, \
        "a running export must count as live or the recording goes stale"
    assert "v.addEventListener('seeked', mark)" in ed, \
        "a seek does not mark the frame dirty, so a scrub paints the previous frame"


def test_the_loop_is_registered_once_and_reads_through_a_ref():
    """The old effect re-subscribed on every render (no deps) and read state
    from its closure. One registration, one ref that render refreshes."""
    ed = _editor()
    i = ed.index("const draw = () => {")
    tail = ed[i:ed.index("}, []);", i)]
    assert "latest.current" in tail, "the loop reads a closure, not the ref"
    assert "cancelAnimationFrame(rafRef.current)" in tail


def test_the_timeline_has_draggable_cut_points_and_a_filmstrip():
    assert "function EdTimeline(" in SRC
    tl = SRC[SRC.index("function EdTimeline("):SRC.index("function ClipEditor(")]
    for handle in ('data-h="in"', 'data-h="out"', 'data-h="head"'):
        assert handle in tl, f"timeline has no {handle} target"
    assert "setPointerCapture(e.pointerId)" in tl, \
        "no pointer capture — a drag dies the moment the finger leaves the strip"
    assert "function buildThumbs(" in SRC and "THUMB_N" in SRC
    assert "tv.muted = true" in SRC, \
        "the thumbnail video is not muted — it would play sound while seeking"


def test_the_cut_points_can_be_set_from_the_keyboard():
    ed = _editor()
    for key, what in (("'i'", "in"), ("'o'", "out"), ("'Escape'", "close"), ("'ArrowLeft'", "step"), ("' '", "play")):
        assert key in ed, f"no {what} shortcut"
    assert "if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return;" in ed, \
        "shortcuts fire while typing in the title box"


def test_the_stage_is_the_framing_control():
    """Drag pans, wheel and pinch zoom. Wheel must be a non-passive listener
    or the page scrolls instead of the picture zooming."""
    ed = _editor()
    assert "el.addEventListener('wheel', onWheel, { passive: false })" in ed
    assert "onPointerDown={stageDown}" in ed and "p.pts.size >= 2" in ed, \
        "no drag-to-pan or no pinch"
    assert "touch-action:none" in SRC[SRC.index(".ed-stage{"):SRC.index(".ed-stage{") + 400]


def test_the_export_stops_the_preview_first_and_time_boxes_every_wait():
    """Exporting while the preview was playing hung the old editor at 0%:
    two things driving the same element. And an export that awaits an event
    that never comes hangs with a Cancel button, which is the worst outcome
    the screen can produce."""
    run = _run_export()
    assert run.lstrip().startswith("// Exporting while the preview is playing") or "pause();" in run[:600], \
        "runExport does not pause the preview before recording"
    rec = _export_recorder()
    assert "Promise.race([g.ctx.resume()" in rec, "AudioContext.resume() is awaited without a timeout"
    assert "await waitFor(v, 'seeked', 3000)" in rec, "the in-point seek is awaited without a timeout"
    assert "span * 1000 + 6000" in rec and "stalled" in rec, "no stall guard on the recording loop"
    assert "Promise.race([finished" in rec, "recorder.onstop is awaited without a timeout"


def test_the_output_size_follows_the_shape_and_ships_the_1080_class_from_720p_up():
    """Platforms re-encode every upload to 1080x1920 with their own scaler; a
    720x1280 file comes out of that softer than the same picture delivered
    at 1080x1920. So the 1080 class is the output for any source of 720p and
    up, and the 720 class is only for sources below that."""
    assert "function outputSize(" in SRC
    body = SRC[SRC.index("function outputSize("):SRC.index("function EdTimeline(")]
    assert "srcShort >= 720" in body, "the HD decision no longer covers 720p sources"
    assert "Math.round(w / 2) * 2" in body and "Math.round(h / 2) * 2" in body, \
        "odd dimensions reach the encoder — H.264 refuses them"
    # 16:9 used to render at 2276x1280 from a 1080p source. Landscape is capped
    # at 1920 wide now.
    assert "h = hd ? 1080 : 720;  w = h * aspect;" in body


def test_the_stage_canvas_is_scaled_by_object_fit_not_by_max_sizes():
    """THE TRAP THIS CAUGHT. As a grid item with max-width/max-height the
    720x1280 canvas sat at native size and was cropped to the top of the
    stage — the captions and the bottom of every frame were simply not on
    screen. Absolute against a definite-height parent + object-fit cannot
    resolve any other way."""
    rule = SRC[SRC.index(".ed-stage canvas{"):]
    rule = rule[:rule.index("}") + 1]
    assert "object-fit:contain" in rule and "position:absolute" in rule
    assert "max-height" not in rule and "max-width" not in rule
    stage = SRC[SRC.index(".ed-stage{"):]
    stage = stage[:stage.index("}") + 1]
    assert "display:block" in stage and "height:min(" in stage


def test_the_editor_is_solid_so_the_page_does_not_bleed_through_on_a_phone():
    """The late @supports .glass rule paints a near-transparent gradient; on a
    phone the editor is the whole screen and the library text showed through."""
    assert ".ed.glass{background:var(--rd-bg-2)}" in SRC


def test_the_editor_side_panel_is_tabbed_and_the_export_is_pinned():
    ed = _editor()
    assert "['trim', 'Shape & length'], ['frame', 'Framing'], ['text', 'Title style']" in ed
    assert "if (captionsOn) TABS.push(['captions', 'Caption style']);" in ed, \
        "the captions tab must follow the release flag like the old panel did"
    assert 'className="ed-foot"' in ed and 'className="rd-btn grad ed-export"' in ed
    css = SRC[SRC.index(".ed-foot{"):SRC.index(".ed-foot{") + 300]
    assert "flex-shrink:0" in css


def test_captions_use_the_dashboard_font_and_light_the_spoken_word():
    """Captions asked for Sora, which the dashboard never loads, and drew the
    fallback. Inter is self-hosted here and preloaded before the first paint.
    With word timings (see transcribe.Segment.words) the spoken word is lit in
    the brand orange; without them the line still renders, unlit."""
    assert "const CAP_FONT = 'Inter," in SRC
    assert "Sora" not in SRC[SRC.index("function drawCaption("):SRC.index("function ClipEditor(")]
    draw = SRC[SRC.index("function drawCaption("):SRC.index("function paintFrame(")]
    assert "cue.words" in draw and "CAP_ACCENT" in draw and "wd.on ? CAP_ACCENT : '#fff'" in draw
    assert "t >= x[0] - 0.05" in draw, "the lit word does not follow the clock"
    assert "o.capUpper" in draw and "toUpperCase()" in draw
    assert "ctx.roundRect" in draw and "strokeText" in draw, "lost a caption style"
    ed = SRC[SRC.index("function ClipEditor("):SRC.index("function UploadScreen(")]
    assert "document.fonts.load('800 40px Inter')" in ed, "caption font is not preloaded"
    for k in ("capUpper", "capWord", "t:"):
        assert k in ed[ed.index("const opts = () => ({"):ed.index("const opts = () => ({") + 400], \
            f"{k} never reaches paintFrame"


# ── templates (2026-09-08) ────────────────────────────────────────────────────

def test_there_are_five_templates_and_each_one_is_a_full_starting_point():
    """One click has to land somewhere complete: shape, layout, fill, framing
    and caption style all set, so nothing from the previous edit leaks
    through. Every knob a template sets must be a knob the editor has."""
    m = re.search(r"const TEMPLATES = \[(.*?)\n\];", SRC, re.S)
    assert m, "TEMPLATES not found"
    ids = re.findall(r"id: '(\w+)'", m.group(1))
    # "auto" is the server's Auto Edit (owner, 2026-09-24), shown to admins.
    assert ids == ["camgame", "full", "blur", "punch", "hook", "auto"], ids
    sets = re.findall(r"set: \{(.*?)\} \}", m.group(1), re.S)
    assert len(sets) == 6
    for body in sets:
        for k in ("ratio", "layout", "fill", "zoom", "offX", "offY", "capPos", "capHi", "capUpper", "capWord", "capSize"):
            assert re.search(r"\b" + k + r":", body), f"a template does not set {k}"
    ed = SRC[SRC.index("function ClipEditor("):SRC.index("function UploadScreen(")]
    setters = re.search(r"const SETTERS = \{(.*?)\};", ed, re.S).group(1)
    for k in ("ratio", "layout", "fill", "zoom", "offX", "offY", "capPos", "capHi", "capUpper", "capWord", "capSize", "textPos", "textSize"):
        assert re.search(r"\b" + k + r": set", setters), f"applyTemplate cannot set {k}"
    assert "setTab(t.tab)" in ed, "a template does not open the tab it wants checked"


def test_the_split_layout_draws_a_camera_window_over_the_whole_frame():
    """Facecam on top (SPLIT_TOP of the height), gameplay at full width below,
    blurred fill behind both — the streamer-clip standard."""
    body = SRC[SRC.index("function paintFrame("):SRC.index("function ClipEditor(")]
    split = body[body.index("if (layout === 'split') {"):body.index("} else if (fill === 'blur') {")]
    assert "SPLIT_TOP" in split and "blurBackdrop(" in split
    assert "ctx.drawImage(video, sx, sy, rw, rh, 0, 0, w, topH)" in split, "no camera window"
    assert "Math.min(w / vw, bh / vh)" in split, "the gameplay panel is not contained at full width"
    assert "if (rh > vh) { rh = vh; rw = rh * w / topH; }" in split, "a loose window can read outside the source"
    o = SRC[SRC.index("const opts = () => ({"):]
    assert "layout" in o[:o.index("});")], "layout never reaches paintFrame"


def test_the_template_row_is_in_the_panel_and_the_layout_is_a_manual_control_too():
    ed = SRC[SRC.index("function ClipEditor("):SRC.index("function UploadScreen(")]
    assert 'className="ed-tpl-row"' in ed and "TEMPLATES.filter(t => !t.server).map(" in ed and "autoEditOn && TEMPLATES.filter(t => t.server).map(" in ed
    assert "onClick={()=>setLayout('single')}" in ed and "setLayout('split')" in ed, \
        "the split layout can only be reached through a template"
    assert "L.layout === 'split'" in ed, "a drag in the split layout does not move the camera window"


# ── export sharpness (2026-09-08) ─────────────────────────────────────────────
#
# "The video gets blurry after editing." Three causes, each pinned: the crop
# resampled with the default bilinear filter, a bitrate budget below what the
# source arrived at, and the Baseline H.264 profile listed first.

def test_the_frame_is_resampled_at_high_quality():
    body = SRC[SRC.index("function paintFrame("):SRC.index("function ClipEditor(")]
    head = body[:body.index("const vw = video.videoWidth")]
    assert "ctx.imageSmoothingEnabled = true;" in head
    assert "ctx.imageSmoothingQuality = 'high'" in head, "the crop is drawn with the default bilinear resampler"


def test_the_export_bitrate_is_above_what_a_twitch_clip_arrives_at():
    """Measured: this recorder spends roughly half of what it is asked for,
    and at 6 Mbps requested the export landed at 1.8 Mbps — below the 6-8
    Mbps a 1080p Twitch clip is delivered at. The budget has to be well
    above the source's, not near it."""
    rec = _export_recorder()
    m = re.search(r"videoBitsPerSecond: hd \? (\d+)e6 : (\d+)e6", rec)
    assert m, "no HD/SD bitrate split"
    assert int(m.group(1)) >= 14 and int(m.group(2)) >= 10, f"bitrate budget too low: {m.groups()}"


def test_high_and_main_h264_profiles_are_preferred_over_baseline():
    m = re.search(r"const REC_TYPES = \[(.*?)\];", SRC, re.S)
    types = re.findall(r"'([^']+)'", m.group(1))
    avc = [t for t in types if "avc1" in t]
    assert avc[0].startswith("video/mp4;codecs=avc1.64"), "High profile is not first"
    assert avc[-1].startswith("video/mp4;codecs=avc1.42"), "Baseline must remain as the floor"


# ── Frame-accurate export (2026-09-15) ───────────────────────────────────────
# MediaRecorder records the canvas in REAL TIME and keeps only the frames the
# encoder finishes in time. Measured on the real paint path in software-rendered
# Chromium: 10.8 fps in the file from captureStream(30), 11.6 from 60, at 1.5
# Mbps against 16 asked for. The WebCodecs path encodes every presented frame at
# its own media time, pauses playback when the encoder falls behind, slows the
# element and seeks back when IT skips, and encodes audio offline. Verified end
# to end (scratchpad/ed/export_fa.js): a 60 fps source produced a 62 fps file
# with 154 frames, every presented frame kept; 30 fps in, 30.4 fps out.

def _fa():
    return SRC[SRC.index("async function exportFrameAccurate("):SRC.index("/* ── Export audio ────")]


def test_the_frame_accurate_path_is_preferred_and_the_recorder_remains():
    body = SRC[SRC.index("const runExport = async () => {"):SRC.index("const clipSecs = Math.max(0, outPt - inPt);")]
    assert "if (faSup) {" in body and "exportFrameAccurate(faSup" in body
    assert "result = await exportRecorder();" in body, "the recorder fallback is gone"
    assert body.index("exportFrameAccurate(faSup") < body.index("exportRecorder();")


def test_both_export_paths_size_the_canvas_to_the_output_first():
    """The preview canvas is stage-sized. An export that recorded or encoded
    it at that size would ship a 400px file."""
    body = SRC[SRC.index("const runExport = async () => {"):SRC.index("const clipSecs = Math.max(0, outPt - inPt);")]
    assert body.index("c.width = outW; c.height = outH;") < body.index("if (faSup) {")


def test_support_is_probed_with_the_real_output_and_a_42_level_first():
    """H.264 level 4.0 is rated to 1080p30; 1080x1920 at 60 needs 4.2. Probing
    a canned 1080p30 config would say yes and then fail to configure."""
    src = SRC[SRC.index("async function frameAccurateSupport("):SRC.index("function _makeMuxer(")]
    assert "width: w, height: h" in src and "framerate: fps" in src
    types = re.findall(r"'(avc1\.[0-9A-Fa-f]+)'", src)
    assert types[0] == "avc1.64002A", "High 4.2 is not tried first"
    assert "vp09" in src and "'opus'" in src, "no VP9/Opus fallback for browsers without H.264/AAC encoders"
    assert "typeof Mp4Muxer !== 'undefined'" in src and "typeof WebMMuxer !== 'undefined'" in src


def test_no_frame_is_ever_dropped_on_purpose():
    """The three mechanisms, each pinned: encoder backpressure pauses playback
    rather than skipping; an element skip halves the rate AND seeks back to
    the last encoded frame; re-presented frames are deduplicated by timestamp
    so the seek-back cannot double-encode."""
    fa = _fa()
    assert "enc.encodeQueueSize > 6 && !v.paused" in fa and "v.pause();" in fa
    assert "md.presentedFrames - lastPresented > 1" in fa
    assert "v.playbackRate = v.playbackRate / 2" in fa
    assert "v.currentTime = inPt + lastTs / 1e6;" in fa, "an element skip is not recovered"
    assert "if (ts > lastTs)" in fa, "re-presented frames would be encoded twice"


def test_frames_carry_media_time_not_wall_time():
    """That is what makes a slower pass produce the same file."""
    fa = _fa()
    assert "const ts = Math.round((mt - inPt) * 1e6);" in fa
    assert "new VideoFrame(c, { timestamp: ts" in fa


def test_every_wait_in_the_frame_accurate_export_is_bounded():
    fa = _fa()
    assert "setTimeout(h, 3000)" in fa, "the in-point seek can hang"
    assert "Date.now() - lastFrameAt > 10000" in fa, "no stall guard"


def test_audio_is_encoded_offline_from_the_source_not_recorded_live():
    src = SRC[SRC.index("async function _encodeAudioOffline("):SRC.index("async function exportFrameAccurate(")]
    assert "decodeAudioData(" in src and "OfflineAudioContext(" in src
    assert "new AudioEncoder(" in src
    assert "return null;" in src, "an unfetchable source must export silent, not fail"


def test_the_muxers_are_vendored_same_origin_with_their_licences():
    """The dashboard cannot load scripts from arbitrary CDNs, and a raw encoder
    stream is a file nobody can open — which is why this path was not wired
    before."""
    import pathlib
    vendor = pathlib.Path(__file__).resolve().parent.parent / "src/dashboard/static/vendor"
    for f in ("mp4-muxer.js", "webm-muxer.js", "mp4-muxer.LICENSE", "webm-muxer.LICENSE"):
        assert (vendor / f).is_file(), f"{f} is missing"
    assert '<script src="/static/vendor/mp4-muxer.js"></script>' in SRC
    assert '<script src="/static/vendor/webm-muxer.js"></script>' in SRC
    assert SRC.index("/static/vendor/webm-muxer.js") < SRC.index('<script type="text/babel">'), \
        "the muxers load after the app that uses them"
    for f in ("mp4-muxer.js", "webm-muxer.js"):
        assert "unpkg.com" not in SRC[SRC.index(f) - 80:SRC.index(f)], f"{f} is loaded from a CDN"


def test_the_export_button_reflects_whichever_path_will_run():
    tail = SRC[SRC.index("const recType = pickRecorderType();"):SRC.index("const framed = zoom !== 1")]
    assert "(!!faSup || !!recType) && dur > 0" in tail
    assert "faSup ? (faSup.ext === 'mp4' ? 'MP4' : 'WebM')" in tail


# ── Preview at display size (2026-09-15) ─────────────────────────────────────

def test_the_preview_is_painted_at_the_size_it_is_shown():
    """A 236px-wide stage does not need a 1080x1920 bitmap behind it.
    Measured identical on screen (Laplacian variance 1374 vs 1376 on the same
    frame) at about a fifth of the pixels per tick — the difference between
    stutter and smooth on a laptop GPU or a phone. Capped at the output size
    so a large monitor never upscales past what export gets."""
    loop = SRC[SRC.index("// ── The paint loop: registered once, reads `latest`. ──"):SRC.index("// A seek lands a new decoded frame")]
    assert "stageSizeRef.current" in loop
    assert "Math.min(1, sw / L.outW, sh / L.outH)" in loop, "the preview can upscale past the output"
    assert "if (!L.busy)" in loop, "an export would be painted at preview size"
    assert "new ResizeObserver(" in SRC[SRC.index("const stageSizeRef"):SRC.index("// ── The paint loop")]


# ── Transitions and sound effects (2026-09-15) ───────────────────────────────
# Transitions are pure functions of media time inside paintFrame/drawCaption,
# so the preview and both exporters get the identical picture. Sounds are
# synthesized with WebAudio and scheduled by ONE function onto whichever
# destination is in play — the live graph for the preview and the recorder,
# the OfflineAudioContext for the frame-accurate export — so the file carries
# exactly what the preview played. Verified in scratchpad/ed/export_fa.js: a
# silent source exported with a whoosh at 0 and a ding at 2.1 s decodes to RMS
# 0.015 / 0 / 0.11 in those windows; luminance 0 at t=0.02 (fade in), 12 at
# the tail (fade out), 44 mid-clip.

def _paint():
    return SRC[SRC.index("function paintFrame("):SRC.index("/* Mirrors src/publish/platforms.py")]


def test_transitions_are_functions_of_the_cut_and_disabled_without_one():
    p = _paint()
    assert "const tin  = (!o.settled && o.inPt  != null && o.t != null) ? (o.t - o.inPt)  : Infinity;" in p
    assert "const tout = (!o.settled && o.outPt != null && o.t != null) ? (o.outPt - o.t) : Infinity;" in p
    for eff in ("o.transIn === 'zoom'", "o.transIn  === 'fade'", "o.transOut === 'fade'"):
        assert eff in p, f"{eff} is not implemented"


def test_the_paused_preview_is_drawn_settled_so_a_fade_in_is_not_a_black_stage():
    """Owner (2026-09-16): "the formats are just making the screen black with
    some clips." Full Frame and Blur Bars fade in from black; the paused
    editor sits at the in-point, where that fade is 100% black and the title
    is still below the frame. The preview loop paints a paused frame with
    `settled`, which paintFrame reads as "no transition here"; playback and
    export (`live`) still paint every transition. Measured in the harness:
    mean luma 0 -> 83 on Full Frame after this."""
    src = SRC[SRC.index("const draw = () => {"):SRC.index("const onMeta = () => {")]
    assert "const o = { ...L.opts(), settled: !live };" in src
    assert "const live = L.playing || L.busy || !v.paused;" in src
    assert src.index("const live = ") < src.index("settled: !live")


def test_the_fade_is_painted_last_over_captions_and_title():
    p = _paint()
    assert p.rindex("ctx.fillRect(0, 0, w, h);") > p.index("if (caption) drawCaption(ctx, caption, o);")
    assert p.rindex("ctx.fillRect(0, 0, w, h);") > p.index("if (text) {")


def test_the_zoom_punch_wraps_every_layout_and_is_restored():
    """A transform left open would scale the captions too, and every frame
    after the first would inherit the previous frame's transform."""
    p = _paint()
    i = p.index("if (punch !== 1) { ctx.translate(w / 2, h / 2);")
    assert i < p.index("if (layout === 'split') {")
    assert "ctx.restore();                              // end of the zoom-punch transform" in p
    assert p.index("// end of the zoom-punch transform") < p.index("if (caption) drawCaption(")


def test_the_caption_word_pop_needs_a_start_time_and_is_scaled_about_its_centre():
    d = SRC[SRC.index("function drawCaption("):SRC.index("function paintFrame(")]
    assert "st: x[0]" in d, "timed cues no longer carry the word's start"
    assert "wd.st != null" in d
    assert "ctx.translate(cx, ly); ctx.scale(s, s); ctx.translate(-cx, -ly);" in d


def test_every_sound_effect_offered_is_implemented():
    kinds = re.findall(r"\['(\w+)', '\w+'\]", SRC[SRC.index("const SFX_KINDS"):SRC.index("const _NOISE")])
    impl = SRC[SRC.index("const SFX = {"):SRC.index("function scheduleSfx(")]
    for k in kinds:
        if k == "none":
            continue
        assert f"  {k}(ctx, dest, at, vol) {{" in impl, f"'{k}' is in the menu but not in SFX"


def test_sound_effects_are_deterministic_and_bounded():
    """Seeded noise so two renders of one clip are byte-identical; every effect
    stops its sources so an offline render cannot run forever."""
    lib = SRC[SRC.index("const _NOISE"):SRC.index("function scheduleSfx(")]
    assert "let s = 12345;" in lib and "Math.random" not in lib
    impl = SRC[SRC.index("const SFX = {"):SRC.index("function scheduleSfx(")]
    assert impl.count(".start(") == impl.count(".stop("), "a source is started and never stopped"


def test_the_export_mixes_the_sound_effects_into_the_offline_render():
    """Same scheduleSfx, onto the OfflineAudioContext, at base 0 — that is what
    makes the file carry what the preview played. And a silent source with
    effects still gets an audio track."""
    a = SRC[SRC.index("async function _encodeAudioOffline("):SRC.index("async function exportFrameAccurate(")]
    assert "scheduleSfx(off, off.destination, sfx, 0)" in a
    assert "if (!decoded && !haveSfx) return null;" in a
    assert "_encodeAudioOffline(sup, srcUrl, inPt, outPt, sfx)" in SRC


def test_the_recorder_export_and_the_preview_share_the_live_graph():
    g = SRC[SRC.index("function audioGraph("):SRC.index("const RATIOS = [")]
    assert "sfx.connect(monitor); sfx.connect(dest);" in g, \
        "effects would be heard but not recorded, or recorded but not heard"
    rec = SRC[SRC.index("const exportRecorder = async () => {"):SRC.index("const runExport = async () => {")]
    assert "scheduleSfx(g.ctx, g.sfx, latest.current.sfx" in rec
    assert "if (L.fireSfx) L.fireSfx(v.currentTime);" in SRC, "play() does not start the sounds"
    assert "if (L.fireSfx) L.fireSfx(L.inPt);" in SRC, "the preview loop does not restart the sounds"


def test_the_frame_accurate_export_receives_the_plan():
    body = SRC[SRC.index("const runExport = async () => {"):SRC.index("const clipSecs = Math.max(0, outPt - inPt);")]
    assert "sfx: latest.current.sfx" in body


def test_templates_carry_effects_and_the_setters_accept_them():
    tpl = SRC[SRC.index("const TEMPLATES = ["):SRC.index("function capWrap(")]
    for k in ("transIn", "transOut", "textAnim", "sfxIn", "sfxOut"):
        assert tpl.count(f"{k}:") == 6, f"not every template sets {k}"
        assert f"{k}: set" in SRC[SRC.index("const SETTERS = {"):SRC.index("const applyTemplate")], \
            f"templates set {k} but applyTemplate cannot apply it"
    for kind in re.findall(r"sfx(?:In|Out): '(\w+)'", tpl):
        assert f"['{kind}', " in SRC[SRC.index("const SFX_KINDS"):SRC.index("const _NOISE")], \
            f"a template names sound '{kind}', which the menu does not offer"


def test_the_editor_side_panel_is_style_title_captions_then_more_options():
    """Owner (2026-09-16): "still a little too confusing, make it even
    simpler." The three things a user does are always in view: pick a style,
    type a title, add captions. Shape, framing, effects and caption style sit
    behind one "More options" button that starts closed. The accordion keeps
    its sections (and its tests) underneath."""
    ed = SRC[SRC.index("function ClipEditor("):SRC.index("function UploadScreen(")]
    side = ed[ed.index('<div className="ed-side">'):ed.index('<div className="ed-foot">')]
    assert "const [showMore, setShowMore] = useState(false);" in ed, "More options is not closed by default"
    # Order: style cards, Title box, Captions box, More options, then the accordion.
    i_style = side.index('className="ed-tpl-row"')
    i_title = side.index('<div className="ed-sec-t">Title</div>')
    i_caps = side.index('<div className="ed-sec-t">Captions</div>')
    i_more = side.index('<span className="ed-sec-l">More options</span>')
    i_panel = side.index('{showMore && <div className="ed-panel" role="tablist">')
    assert i_style < i_title < i_caps < i_more < i_panel, "the side panel order changed"
    # The title is typed in the quick box (the one textarea) and the accordion's
    # Title style section only styles it.
    assert side.count('<textarea className="ed-in"') == 1, "the title is typed in two places"
    assert side.index('<textarea className="ed-in"') < i_more
    # Captions are generated from the quick box; the accordion styles them.
    quick_caps = side[i_caps:i_more]
    assert "onClick={makeCaptions}" in quick_caps and "setCapOn(v=>!v)" in quick_caps
    acc_caps = side[side.index("{k==='captions' && captionsOn"):]
    assert "makeCaptions" not in acc_caps and "setCapOn" not in acc_caps
    assert "setCapPos(" in acc_caps and "setCapHi(" in acc_caps
    # No numbered steps left over from the previous layout.
    assert "Pick a style" not in side and "<b>2</b>" not in side


def test_the_effects_tab_exists_and_exposes_every_knob():
    assert "['fx', 'Effects']" in SRC
    # The Effects controls live in the accordion section keyed 'fx' (2026-09-15
    # simplification: sections with a one-line summary replaced the tab strip).
    ui = SRC[SRC.index("{k==='fx' && <div className=\"ed-grp\">"):SRC.index("{k==='captions' && captionsOn")]
    for setter in ("setTransIn", "setTransOut", "setSfxIn", "setSfxOut", "setSfxGain"):
        assert setter in ui, f"the Effects section has no control for {setter}"
    # The title's own animation sits with the title, in the Text section.
    text_ui = SRC[SRC.index("{k==='text' && <div className=\"ed-grp\">"):SRC.index("{k==='fx' && <div")]
    assert "setTextAnim" in text_ui, "the Text section has no 'Title rises in' toggle"
