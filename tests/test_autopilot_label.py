"""Autopilot is named where clips are decided and where they are edited.

Owner, 2026-11-19: "i need the autopilot to be labeled in the clip review tab
and the editor so people use it more". Autopilot had one door, its own tab.
Held here:
  * one reading of its state for both places (held back: nothing; not on the
    plan: named, as Pro; otherwise on or off), run as the browser runs it;
  * Clip Review gets a pill IN its title line, not a row above the clips;
  * the Clip Editor gets a line under its header;
  * pressing either OPENS the Autopilot tab and never flips the switch
    (switching it on starts it through every clip already approved);
  * it follows the switch live, from the state the app already keeps current.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

SRC = Path("src/dashboard/aurora_html.py").read_text()
CSS = SRC.split('<script type="text/babel">')[0]
JS = SRC.split('<script type="text/babel">')[1]


def _fn(name: str) -> str:
    start = JS.index(f"function {name}(")
    return JS[start:JS.index("\n}\n", start) + 3]


def _state(me, ap, uploads_on):
    code = _fn("autopilotState") + f"\nconsole.log(JSON.stringify(autopilotState({json.dumps(me)}, {json.dumps(ap)}, {json.dumps(uploads_on)})));"
    got = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert got.returncode == 0, got.stderr
    return json.loads(got.stdout.strip())


PRO = {"plan_limits": {"uploads": True}}
FREE = {"plan_limits": {"uploads": False}}
ON, OFF = {"config": {"enabled": True}}, {"config": {"enabled": False}}


@pytest.mark.parametrize("me, ap, uploads_on, want", [
    (PRO, ON, True, "on"),
    (PRO, OFF, True, "off"),
    (FREE, None, True, "pro"),                    # /autopilot answers 403 below Pro: there is no config
    (FREE, ON, True, "pro"),
    ({"plan_limits": {"uploads": False}, "is_admin": True}, OFF, True, "off"),   # staff are always through
    (PRO, None, True, None),                      # settings not here yet: say nothing rather than guess
    (PRO, {}, True, None),
    (PRO, ON, False, None),                       # held back for everybody: nothing to open
    (FREE, None, False, None),
    (None, ON, True, None),                       # /me not loaded
])
def test_one_reading_of_the_state_for_both_places(me, ap, uploads_on, want):
    assert _state(me, ap, uploads_on) == want


def test_clip_review_names_it_in_the_title_line_not_in_a_row_of_its_own():
    """Nothing may be added above the first clip on this screen
    (tests/test_dashboard_density.py): the pill goes where the counts are."""
    body = JS[JS.index("function ReviewScreen("):JS.index("\nfunction ", JS.index("function ReviewScreen(") + 10)]
    assert body.count("<AutopilotChip") == 1
    bar = body[body.index('className="rd-toolbar-count"'):body.index('<ClipControls')]
    assert "<AutopilotChip me={me} ap={autopilot} uploadsOn={uploadsOn} onOpen={onOpenAutopilot}/>" in bar
    assert bar.index("<AutopilotChip") < bar.index('className="rd-toolbar-acts"'), \
        "it belongs with what the screen tells you, before the buttons that change things"
    assert "autopilot = null, uploadsOn = false, onOpenAutopilot = null" in body[:600]


def test_the_clip_editor_names_it_under_its_header():
    body = JS[JS.index("function UploadScreen("):JS.index("\nfunction ", JS.index("function UploadScreen(") + 10)]
    assert body.count("<AutopilotStrip") == 1
    assert body.index('className="edp-bar rd-lib-head"') < body.index("<AutopilotStrip") < body.index("showHow && <div")
    assert "<AutopilotStrip me={me} ap={autopilot} uploadsOn={uploadsOn} onOpen={onOpenAutopilot}/>" in body


def test_the_app_hands_both_screens_the_live_state_and_the_way_to_the_tab():
    review = next(ln for ln in JS.splitlines() if "view==='review'" in ln)
    assert "autopilot,uploadsOn,onOpenAutopilot:()=>setRoute('autopilot')" in review
    editor = JS[JS.index("else if(view==='uploads') screen=<UploadScreen"):]
    editor = editor[:editor.index("/>;") + 3]
    assert "autopilot={autopilot} onOpenAutopilot={()=>setRoute('autopilot')}" in editor
    # The state it reads is the one the socket and every reconnect keep current.
    assert "else if(msg.event==='autopilot_changed'){" in JS
    assert "setAutopilot(a=>({...(a||{}), config: msg.config}));" in JS
    # refetchAll (mount and every reconnect) -> refetchPublishing, once /me
    # says the account can post -> refetchAutopilot.
    publishing = JS[JS.index("const refetchPublishing = useCallback"):JS.index("const publishLoaded = useRef")]
    assert "refetchAutopilot();" in publishing
    start = JS.index("const refetchAll = useCallback")
    assert "refetchPublishing()" in JS[start:JS.index("window.dispatchEvent(new CustomEvent('hz_refetch'))", start)]


def test_pressing_it_opens_the_tab_and_never_flips_the_switch():
    """Switching Autopilot on starts it through every clip already approved.
    That belongs to the screen that says so."""
    for name in ("AutopilotChip", "AutopilotStrip"):
        body = JS[JS.index(f"function {name}("):JS.index("\n}\n", JS.index(f"function {name}(")) + 3]
        assert "fetch(" not in body and "PUT" not in body, name
        assert "onOpen" in body
        assert "autopilotState(me, ap, uploadsOn)" in body and "if (!st) return null;" in body


def test_what_it_says_in_each_state():
    words = JS[JS.index("const AP_LABEL = {"):JS.index("};", JS.index("const AP_LABEL = {"))]
    assert "chip: 'Autopilot on', act: 'Open Autopilot'" in words
    assert "chip: 'Autopilot off · turn on', act: 'Turn on Autopilot'" in words
    assert "chip: 'Autopilot · Pro', act: 'See Autopilot'" in words
    strip = JS[JS.index("function AutopilotStrip("):JS.index("\n}\n", JS.index("function AutopilotStrip("))]
    assert "<b>Autopilot is on.</b> Every clip you approve is edited for vertical and added to the Scheduler by itself." in strip
    assert "<b>Autopilot is off.</b> It can make this edit for you" in strip
    assert "It is included with Pro." in strip
    # It promises what the Autopilot tab itself promises, and no more.
    assert "Accept a clip and Autopilot edits it for vertical" in JS
    for word in ("captions", "posted for you", "TikTok"):
        assert word not in words and word not in strip, word


def test_it_is_a_button_with_a_name_and_wears_the_themes_own_colours():
    chip = JS[JS.index("function AutopilotChip("):JS.index("\n}\n", JS.index("function AutopilotChip("))]
    assert '<button type="button"' in chip and "aria-label={AP_LABEL[st].tip}" in chip
    assert re.search(r"\.rd-apchip\.off\{[^}]*background:var\(--grad-soft\)", CSS)
    assert re.search(r"\.rd-apchip\.on\{[^}]*background:var\(--live-soft\)", CSS)
    rules = "".join(re.findall(r"\.rd-ap(?:chip|strip)[^{]*\{[^}]*\}", CSS))
    assert "184,106,220" not in rules and "#c489e4" not in rules, "a purple that the Kick theme would not change"
    assert "white-space:nowrap" in rules
