"""Ordering clips — the same way on every screen that shows them.

WHY NODE AND NOT A REGEX. The comparator is a handful of lines of JS living
inside a Python string, and every interesting thing about it is behaviour: what
a missing field sorts to, when the pending-first grouping applies, what breaks a
tie. A test that greps for `sortDir === 'asc'` passes just as happily when the
two branches are swapped. So these tests cut the REAL comparator out of
aurora_html.py and run it over fixture clips in node.

WHY IT IS SHARED. Clip Review and Clip Library render the SAME cards from the
same store, and they had drifted: Review got a sort menu and a styled streamer
picker while the Library kept a bare <select> and no ordering at all. One set of
clips, sortable on one screen and not the other. The keys, the comparator and
the direction wording now live in one place and both screens use them; each
still picks WHICH sorts it offers and what it defaults to.

THE FIXTURE IS DELIBERATELY CROSSED. Newest, highest trigger and highest
virality are three different clips, and no ordering is a rotation of another. A
fixture where the newest clip also has the top score cannot tell a working sort
from one that ignores its key.
"""

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

SRC = Path("src/dashboard/aurora_html.py").read_text()
JS = SRC.split('<script type="text/babel">')[1]
NODE = shutil.which("node") or "/opt/node22/bin/node"

pytestmark = pytest.mark.skipif(
    not Path(NODE).exists(), reason="node is not installed here")


# ── cutting the real code out of the page ────────────────────────────────────

def _sort_block() -> str:
    """Keys, comparator and direction wording, exactly as they ship."""
    m = re.search(r"\nconst CLIP_SORTS = \{.*?\nfunction dirLabelFor\(.*?\n\}\n", JS, re.S)
    assert m, "the shared sort block moved — this test is no longer testing anything"
    return m.group(0)


def _screen_sorts(name: str) -> list:
    """The sort options one screen actually offers, in its own order."""
    if name == "review":
        m = re.search(r"const SORTS = \[([^\]]*)\]", JS)
    else:
        m = re.search(r"sorts=\{\[([^\]]*)\]\}", JS)
    assert m, f"{name} screen's sort list not found"
    return re.findall(r"'([a-z]+)'", m.group(1))


# Crossed on purpose: newest is 'a', top trigger is 'b', top virality is 'c',
# and 'd' is last by date but not last by either score. approved_at is set so
# the library's default sort is distinguishable from capture order.
CLIPS = [
    {"id": "a", "status": "approved", "channel": "nova",    "created_at": 400,
     "approved_at": 100, "trigger_score": 70, "virality_score": 60},
    {"id": "b", "status": "pending",  "channel": "kestrel", "created_at": 300,
     "trigger_score": 95, "virality_score": 20},
    {"id": "c", "status": "rejected", "channel": "nova",    "created_at": 200,
     "approved_at": 400, "trigger_score": 80, "virality_score": 96},
    {"id": "d", "status": "approved", "channel": "kestrel", "created_at": 100,
     "approved_at": 300, "trigger_score": 10, "virality_score": 55},
]


def _run(clips=None, sort_by="newest", sort_dir="desc", pending_first=True):
    """Sort `clips` with the shipped comparator. Returns ids and the dir label."""
    harness = textwrap.dedent("""
    %s
    const clips = %s;
    const shown = sortClips(clips, %s, %s, %s);
    console.log(JSON.stringify({
      ids: shown.map(c => c.id),
      dirLabel: dirLabelFor(%s, %s),
      known: Object.keys(CLIP_SORTS),
      labels: Object.keys(CLIP_SORTS).map(k => k + '|' + CLIP_SORTS[k].l),
    }));
    """) % (
        _sort_block(),
        json.dumps(CLIPS if clips is None else clips),
        json.dumps(sort_by), json.dumps(sort_dir),
        "true" if pending_first else "false",
        json.dumps(sort_by), json.dumps(sort_dir),
    )
    out = subprocess.run([NODE, "-e", harness], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ── the score sorts, both directions ─────────────────────────────────────────

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


# ── the date sorts ───────────────────────────────────────────────────────────

def test_the_review_default_is_the_work_queue_pending_first_then_newest():
    """This screen is a queue before it is a gallery. 'b' is the only pending
    clip so it leads; then the approved pair newest-first (a, d); then the
    rejected one. Status decides the groups, date decides within them."""
    assert _run()["ids"] == ["b", "a", "d", "c"]


def test_the_library_orders_by_when_you_APPROVED_not_when_it_was_captured():
    """The library is the record of what you decided to keep. Ordering by
    capture time meant a clip you had just approved could land pages down,
    which reads as "my approval did nothing"."""
    assert _run(sort_by="approved", pending_first=False)["ids"] == ["c", "b", "d", "a"]
    # and that is genuinely a different answer from capture order
    assert _run(sort_by="newest", pending_first=False)["ids"] == ["a", "b", "c", "d"]


def test_a_clip_approved_before_that_field_existed_falls_back_to_capture_time():
    """'b' has no approved_at. It must sort by when it was CAPTURED rather than
    collapsing to 0 and pinning itself to the bottom for ever — which is where
    every clip approved before that field shipped would then live."""
    got = _run(sort_by="approved", pending_first=False)["ids"]
    assert got[-1] != "b", "a clip with no approval timestamp sank to the bottom"
    assert got.index("b") < got.index("a"), \
        "'b' (captured at 300) should outrank 'a' (approved at 100)"


def test_pending_first_does_NOT_apply_to_an_explicit_score_sort():
    """THE POINT OF THE FEATURE. Asking for the highest trigger score and
    getting a wall of already-reviewed clips above a 95 is not sorting by
    trigger score."""
    for field in ("trigger", "virality"):
        ids = _run(sort_by=field, sort_dir="desc")["ids"]
        best = max(CLIPS, key=lambda c: c[field + "_score"])
        assert ids[0] == best["id"], f"{field}: status grouping outranked the score"


def test_a_screen_that_does_not_ask_for_grouping_does_not_get_it():
    """The library lists approved clips only, so grouping by status there would
    be sorting on a column with one value in it."""
    assert _run(pending_first=False)["ids"] == ["a", "b", "c", "d"]


def test_the_date_sort_can_still_be_flipped_to_oldest_first():
    """Within each status group — pending still leads."""
    assert _run(sort_dir="asc")["ids"] == ["b", "d", "a", "c"]


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
    for field in ("newest", "approved", "trigger", "virality"):
        for direction in ("asc", "desc"):
            ids = _run(sort_by=field, sort_dir=direction)["ids"]
            assert sorted(ids) == ["a", "b", "c", "d"], (field, direction)


def test_the_original_array_is_not_mutated_in_place():
    """The list comes from React state. Sorting it in place mutates state
    outside setState, so a re-render can show a different order than the one
    that was just computed."""
    assert "[...list].sort(" in _sort_block(), \
        "the comparator sorts the caller's array in place"


# ── the direction control says what it will do ───────────────────────────────

@pytest.mark.parametrize("field,direction,label", [
    ("newest",   "desc", "Newest first"),
    ("newest",   "asc",  "Oldest first"),
    ("approved", "desc", "Newest first"),
    ("approved", "asc",  "Oldest first"),
    ("trigger",  "desc", "High to low"),
    ("trigger",  "asc",  "Low to high"),
    ("virality", "desc", "High to low"),
    ("virality", "asc",  "Low to high"),
])
def test_the_direction_label_uses_the_words_that_fit_the_field(field, direction, label):
    """"Ascending" on a date column is a small riddle. The label adapts."""
    assert _run(sort_by=field, sort_dir=direction)["dirLabel"] == label


def test_every_sort_is_named_for_a_human():
    assert _run()["labels"] == [
        "newest|Date added", "approved|Date approved",
        "trigger|Trigger score", "virality|Virality"]


@pytest.mark.parametrize("screen,expected", [
    ("review",  ["newest", "trigger", "virality"]),
    ("library", ["approved", "newest", "trigger", "virality"]),
])
def test_each_screen_offers_the_sorts_that_make_sense_for_it(screen, expected):
    """Shared machinery, screen-specific menu. The library leads with the date
    it was approved and Review leads with the date it arrived, because one is an
    archive and the other is a queue."""
    assert _screen_sorts(screen) == expected


@pytest.mark.parametrize("screen", ["review", "library"])
def test_every_offered_sort_exists(screen):
    """An option in a menu with no entry in CLIP_SORTS silently falls back to
    date — the menu changes and the grid does not."""
    known = _run()["known"]
    for v in _screen_sorts(screen):
        assert v in known, f"{screen} offers {v!r}, which is not a real sort"


# ── the two clip screens must not drift apart again ──────────────────────────

def _screen(name: str) -> str:
    m = re.search(r"function " + name + r"\(.*?\n\}\n\n", JS, re.S)
    assert m, f"{name} not found"
    return m.group(0)


@pytest.mark.parametrize("screen", ["ReviewScreen", "LibraryScreen"])
def test_neither_clip_screen_writes_its_own_sort(screen):
    """Two copies of a comparator is how one screen ends up sortable and the
    other does not."""
    body = _screen(screen)
    assert "sortClips(" in body, f"{screen} does not use the shared sort"
    assert ".sort((a,b)" not in body, f"{screen} has its own comparator again"


@pytest.mark.parametrize("screen", ["ReviewScreen", "LibraryScreen"])
def test_neither_clip_screen_writes_its_own_controls(screen):
    body = _screen(screen)
    assert "<ClipControls" in body, f"{screen} builds its own controls row"
    assert "<select" not in body, (
        f"{screen} has a native <select> again. Its popup is drawn by the "
        f"operating system, so it ignores this stylesheet entirely and lands "
        f"as a grey system menu in the middle of a dark app.")


def test_the_library_can_be_sorted_at_all():
    """It could not. Review had three sorts and the library had none, on the
    same clips."""
    body = _screen("LibraryScreen")
    assert "sortBy" in body and "setSortDir" in body
    # Rendered whenever there is anything to sort — not present-but-disabled.
    # "<ClipControls appears in the file" would still be true behind a `false`.
    assert "{approved.length>0 && <ClipControls" in body, \
        "the library's controls are no longer rendered from a real condition"


# ── the dropdowns ────────────────────────────────────────────────────────────

def _rdmenu() -> str:
    m = re.search(r"function RdMenu\(\{.*?\n\}\n", JS, re.S)
    assert m, "RdMenu not found"
    return m.group(0)


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
    toolbar = re.search(r'<div className="rd-toolbar">.*?\n        </div>', JS, re.S)
    assert toolbar, "the toolbar row is gone"
    assert "Cull clips" in toolbar.group(0)
    assert "ClearQueueButton" in toolbar.group(0)
    assert "ClipControls" not in toolbar.group(0)


def test_every_class_the_controls_use_is_actually_styled():
    """No bundler and no CSS modules here — a class name typo is invisible
    until someone looks at the page, and only on the one screen that uses it.

    Read in the direction that catches a typo: take the class names the JSX
    actually asks for and require a rule for each. Checking that the rules
    exist proves nothing — a rule nobody references still exists."""
    css = SRC.split('<script type="text/babel">')[0]
    styled = set(re.findall(r"\.(rd-[a-z-]+)", css))
    used = set()
    for span in (_rdmenu(), _screen("ClipControls"), _screen("LibraryScreen"),
                 re.search(r'<div className="rd-toolbar">.*?\n        </div>', JS, re.S).group(0)):
        used |= set(re.findall(r"'(rd-[a-z-]+)'", span))
        used |= set(re.findall(r'className="(rd-[a-z-]+)"', span))
    assert used, "no class names found — the extraction broke, not the CSS"
    missing = sorted(used - styled)
    assert not missing, "used but never styled: " + str(missing)
    for cls in ("rd-controls", "rd-sortwrap", "rd-dir", "rd-menu", "rd-cliphead"):
        assert cls in used, f"{cls} is no longer used"


def test_the_icons_the_controls_ask_for_exist():
    """<Icon name="..."/> with an unknown name renders nothing at all."""
    m = re.search(r"const Icon = .*?\n  const P = \{(.*?)\n  \};", JS, re.S)
    assert m, "the icon map moved"
    for name in ("chevron", "arrowdown", "arrowup", "sliders", "check", "radio"):
        assert re.search(r"\n    " + name + r":", m.group(1)), f"icon {name!r} missing"
