"""Sorting on the clip review screen — run as JavaScript, not read as text.

WHY NODE AND NOT A REGEX. The comparator is three lines of JS living inside a
Python string, and every interesting thing about it is behaviour: what a
missing field sorts to, when the pending-first grouping applies, what breaks a
tie. A test that greps for `sortDir === 'asc'` passes just as happily when the
two branches are swapped. So these tests cut the REAL comparator out of
aurora_html.py and run it over fixture clips in node. If the shipped source
changes meaning, they fail; if it is only reformatted, they do not.

THE FIXTURE IS DELIBERATELY CROSSED. Newest, highest trigger and highest
virality are three different clips, and none of the three orders is a rotation
of another. A fixture where the newest clip also has the top score cannot tell
a working sort from one that ignores its key.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SRC = Path("src/dashboard/aurora_html.py").read_text()
NODE = shutil.which("node") or "/opt/node22/bin/node"

pytestmark = pytest.mark.skipif(
    not Path(NODE).exists(), reason="node is not installed here")


# ── cutting the real code out of the page ────────────────────────────────────

def _sort_block() -> str:
    """The comparator exactly as it ships, from SORT_KEY to the end of sort()."""
    m = re.search(r"\n  const SORT_KEY = \{.*?\n  \}\);\n", SRC, re.S)
    assert m, "the sort block moved — this test is no longer testing anything"
    return m.group(0)


def _labels_block() -> str:
    """The sort options and the direction label, as they ship."""
    m = re.search(r"\n  const SORTS = \[.*?dirLabel = .*?;\n", SRC, re.S)
    assert m, "the SORTS/dirLabel block moved"
    return m.group(0)


# Crossed on purpose: newest is 'a', top trigger is 'b', top virality is 'c',
# and 'd' is last by date but not last by either score.
CLIPS = [
    {"id": "a", "status": "pending",  "channel": "nova",    "created_at": 400,
     "trigger_score": 70, "virality_score": 60},
    {"id": "b", "status": "pending",  "channel": "kestrel", "created_at": 300,
     "trigger_score": 95, "virality_score": 20},
    {"id": "c", "status": "rejected", "channel": "nova",    "created_at": 200,
     "trigger_score": 80, "virality_score": 96},
    {"id": "d", "status": "approved", "channel": "kestrel", "created_at": 100,
     "trigger_score": 10, "virality_score": 55},
]


def _run(clips=None, sort_by="newest", sort_dir="desc"):
    """Sort `clips` with the shipped comparator. Returns (ids, dirLabel)."""
    harness = textwrap.dedent("""
    const filtered = %s;
    const sortBy = %s, sortDir = %s;
    %s
    %s
    console.log(JSON.stringify({
      ids: shown.map(c => c.id),
      dirLabel: dirLabel,
      sorts: SORTS.map(s => s.v + '|' + s.l),
    }));
    """) % (
        json.dumps(CLIPS if clips is None else clips),
        json.dumps(sort_by), json.dumps(sort_dir),
        _sort_block(), _labels_block(),
    )
    out = subprocess.run([NODE, "-e", harness], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── the request: sort by trigger score, and by virality, both directions ─────

def test_trigger_score_high_to_low():
    assert _run(sort_by="trigger", sort_dir="desc")["ids"] == ["b", "c", "a", "d"]


def test_trigger_score_low_to_high():
    assert _run(sort_by="trigger", sort_dir="asc")["ids"] == ["d", "a", "c", "b"]


def test_virality_high_to_low():
    assert _run(sort_by="virality", sort_dir="desc")["ids"] == ["c", "a", "d", "b"]


def test_virality_low_to_high():
    assert _run(sort_by="virality", sort_dir="asc")["ids"] == ["b", "d", "a", "c"]


def test_the_two_score_sorts_do_not_produce_the_same_order():
    """If they agreed, either field could be being read for both."""
    assert _run(sort_by="trigger")["ids"] != _run(sort_by="virality")["ids"]


def test_reversing_the_direction_reverses_the_order():
    for field in ("trigger", "virality"):
        down = _run(sort_by=field, sort_dir="desc")["ids"]
        up = _run(sort_by=field, sort_dir="asc")["ids"]
        assert up == list(reversed(down)), field


# ── the review queue behaviour that had to survive ───────────────────────────

def test_the_default_is_still_the_review_queue_pending_first_then_newest():
    """This screen is a work queue before it is a gallery. 'a' and 'b' are
    pending so they come first, newest inside that group; then approved 'd',
    then rejected 'c' — status order, not date order, across the groups."""
    assert _run()["ids"] == ["a", "b", "d", "c"]


def test_pending_first_does_NOT_apply_to_an_explicit_score_sort():
    """THE POINT OF THE FEATURE. Asking for the highest trigger score and
    getting a wall of already-reviewed clips above a 95 is not sorting by
    trigger score. Only the date sort groups by status."""
    for field in ("trigger", "virality"):
        ids = _run(sort_by=field, sort_dir="desc")["ids"]
        top = next(c for c in CLIPS if c["id"] == ids[0])
        best = max(CLIPS, key=lambda c: c[field + "_score"])
        assert top["id"] == best["id"], \
            f"{field}: status grouping outranked the score"


def test_the_date_sort_can_still_be_flipped_to_oldest_first():
    """Within a status group. Pending 'b' (older) now precedes pending 'a'."""
    assert _run(sort_dir="asc")["ids"] == ["b", "a", "d", "c"]


# ── the edges that make a comparator silently do nothing ─────────────────────

@pytest.mark.parametrize("field,expect", [
    ("virality", ["c", "d", "b", "a"]),
    ("trigger",  ["b", "c", "d", "a"]),
])
def test_a_clip_missing_the_field_sorts_to_the_bottom_not_to_NaN(field, expect):
    """Clips captured before a score existed have no value for it. `undefined -
    80` is NaN, and a comparator returning NaN does not sort — it leaves the
    array in whatever order it was already in, with no error anywhere."""
    clips = [dict(c) for c in CLIPS]
    del clips[0][field + "_score"]           # 'a'
    ids = _run(clips, sort_by=field, sort_dir="desc")["ids"]
    assert ids == expect, f"a clip with no {field} score did not sort to the bottom"
    assert ids[-1] == "a"


def test_every_clip_missing_the_field_still_yields_a_usable_order():
    """The all-NaN case: nothing to rank on, so it must fall through to the
    tie-break and come back newest-first rather than arbitrary."""
    clips = [{k: v for k, v in c.items() if k != "trigger_score"}
             for c in reversed(CLIPS)]
    assert _run(clips, sort_by="trigger")["ids"] == ["a", "b", "c", "d"]


def test_ties_break_on_newest_rather_than_arbitrarily():
    """Virality is banded and a quiet stream produces runs of identical trigger
    scores, so ties are the common case, not the exotic one.

    Fed in OLDEST-first on purpose: JS sort is stable, so a comparator that
    returns 0 for a tie hands back the input order untouched, and a fixture
    already in the right order cannot tell that apart from a real tie-break."""
    clips = [dict(c, trigger_score=50) for c in reversed(CLIPS)]
    assert _run(clips, sort_by="trigger")["ids"] == ["a", "b", "c", "d"]
    # And the tie-break is not itself flipped by the direction control: a tie
    # has no direction, so both give the same, stable answer.
    assert _run(clips, sort_by="trigger", sort_dir="asc")["ids"] == ["a", "b", "c", "d"]


def test_an_unknown_sort_field_falls_back_to_the_date_key():
    """State can outlive a rename — a saved 'top' from an older build must not
    empty the grid or throw."""
    got = _run(sort_by="top")["ids"]
    assert sorted(got) == ["a", "b", "c", "d"]


def test_sorting_never_drops_or_duplicates_a_clip():
    for field in ("newest", "trigger", "virality"):
        for direction in ("asc", "desc"):
            ids = _run(sort_by=field, sort_dir=direction)["ids"]
            assert sorted(ids) == ["a", "b", "c", "d"], (field, direction)


def test_the_original_array_is_not_mutated_in_place():
    """`clips` comes from React state. Sorting it in place mutates state
    outside setState, so a re-render can show a different order than the one
    that was just computed."""
    assert "[...filtered].sort(" in _sort_block(), \
        "the comparator sorts the state array in place"


# ── the direction control says what it will do ───────────────────────────────

@pytest.mark.parametrize("field,direction,label", [
    ("newest",   "desc", "Newest first"),
    ("newest",   "asc",  "Oldest first"),
    ("trigger",  "desc", "High to low"),
    ("trigger",  "asc",  "Low to high"),
    ("virality", "desc", "High to low"),
    ("virality", "asc",  "Low to high"),
])
def test_the_direction_label_uses_the_words_that_fit_the_field(field, direction, label):
    """"Ascending" on a date column is a small riddle. The label adapts."""
    assert _run(sort_by=field, sort_dir=direction)["dirLabel"] == label


def test_every_sort_option_is_reachable_and_named():
    got = _run()["sorts"]
    assert got == ["newest|Date added", "trigger|Trigger score", "virality|Virality"]


def test_every_offered_sort_has_a_key_that_reads_a_real_clip_field():
    """An option in the menu with no entry in SORT_KEY silently falls back to
    date — the menu changes and the grid does not."""
    block = _sort_block()
    for opt in _run()["sorts"]:
        v = opt.split("|")[0]
        assert re.search(r"\n\s+" + v + r":\s", block), \
            f"sort option {v!r} has no SORT_KEY entry"


# ── the dropdowns ────────────────────────────────────────────────────────────

def _rdmenu() -> str:
    m = re.search(r"function RdMenu\(\{.*?\n\}\n", SRC, re.S)
    assert m, "RdMenu not found"
    return m.group(0)


def test_the_review_controls_use_the_styled_menu_and_not_a_native_select():
    """A native <select> popup is drawn by the OS: its background, font and
    highlight ignore this stylesheet entirely and land as a grey system menu in
    the middle of a dark app."""
    m = re.search(r'<div className="rd-controls">.*?\n        </div>', SRC, re.S)
    assert m, "the review controls row moved"
    controls = m.group(0)
    assert "<select" not in controls, "a native <select> is back in the toolbar"
    assert controls.count("<RdMenu") == 2, \
        "expected the streamer filter and the sort field to be the same control"


def test_the_menu_only_holds_document_listeners_while_it_is_open():
    """A long-lived tab with a dozen of these each holding a permanent document
    mousedown listener is how the dashboard starts feeling slow."""
    body = _rdmenu()
    assert re.search(r"if\(!open\) return;", body), \
        "listeners are bound even while the menu is closed"
    assert body.count("removeEventListener") == body.count("addEventListener"), \
        "a listener is added without being removed"
    assert re.search(r"\}, \[open\]\);", body), \
        "the effect does not re-run when the menu opens or closes"


def test_the_menu_closes_without_the_mouse():
    assert "'Escape'" in _rdmenu(), "Escape does not close the menu"


def test_the_menu_falls_back_rather_than_rendering_blank():
    """The selected streamer can vanish mid-session when their last clip is
    culled. A menu whose button label is empty looks broken."""
    assert re.search(r"options\.find\(o => o\.v === value\) \|\| options\[0\]",
                     _rdmenu()), "no fallback when the selected value is gone"


def test_the_menu_is_announced_as_a_listbox():
    body = _rdmenu()
    for attr in ('aria-haspopup="listbox"', "aria-expanded={open}",
                 'role="listbox"', 'role="option"', "aria-selected="):
        assert attr in body, f"missing {attr}"


def test_the_destructive_actions_are_not_in_the_same_row_as_the_view_controls():
    """Cull and Clear queue delete things; the filters and the sort only change
    what you are looking at. They used to sit in one undifferentiated run of
    five controls, with a bulk delete inches from a sort toggle."""
    toolbar = re.search(r'<div className="rd-toolbar">.*?\n        </div>', SRC, re.S)
    controls = re.search(r'<div className="rd-controls">.*?\n        </div>', SRC, re.S)
    assert toolbar and controls, "the two toolbar rows are not both present"
    assert "Cull clips" in toolbar.group(0)
    assert "ClearQueueButton" in toolbar.group(0)
    assert "Cull clips" not in controls.group(0)
    assert "ClearQueueButton" not in controls.group(0)
    assert "RdMenu" not in toolbar.group(0)


def test_every_class_the_new_toolbar_uses_is_actually_styled():
    """No bundler and no CSS modules here — a class name typo is invisible
    until someone looks at the page, and only on the one screen that uses it.

    Read in the direction that catches a typo: take the class names the JSX
    actually asks for and require a rule for each. Checking that the rules
    exist proves nothing — a rule nobody references still exists."""
    css = SRC.split('<script type="text/babel">')[0]
    styled = set(re.findall(r"\.(rd-[a-z-]+)", css))
    used = set()
    for span in (_rdmenu(),
                 re.search(r'<div className="rd-toolbar">.*?\n        </div>', SRC, re.S).group(0),
                 re.search(r'<div className="rd-controls">.*?\n        </div>', SRC, re.S).group(0)):
        used |= set(re.findall(r"'(rd-[a-z-]+)'", span))
        used |= set(re.findall(r'className="(rd-[a-z-]+)"', span))
    assert used, "no class names found — the extraction broke, not the CSS"
    missing = sorted(used - styled)
    assert not missing, "used in the review toolbar but never styled: " + str(missing)
    # And the ones the redesign introduced are genuinely among them.
    for cls in ("rd-toolbar-count", "rd-toolbar-acts", "rd-controls",
                "rd-sortwrap", "rd-dir", "rd-menu", "rd-menu-btn"):
        assert cls in used, f"{cls} is no longer used by the toolbar"


def test_the_icons_the_toolbar_asks_for_exist():
    """<Icon name="..."/> with an unknown name renders nothing at all."""
    m = re.search(r"const Icon = .*?\n  const P = \{(.*?)\n  \};", SRC, re.S)
    assert m, "the icon map moved"
    icons = m.group(1)
    for name in ("chevron", "arrowdown", "arrowup", "sliders", "check", "radio"):
        assert re.search(r"\n    " + name + r":", icons), f"icon {name!r} missing"
