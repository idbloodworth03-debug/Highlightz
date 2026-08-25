"""Two things the dashboard was doing to the person using it.

1. THE SCREEN FOR REVIEWING CLIPS SHOWED NO CLIPS. 365px of fixed chrome stood
   between the top of the window and the first card: the trial banner, four big
   stat tiles, and a second "Clip review" heading under the one already in the
   page header. Measured in a browser at three laptop sizes, the first clip
   started at y=365 on every one of them — 52% of a 1366x700 screen, on which
   exactly ZERO clips were fully visible. That is the screen the product exists
   for, and on a common laptop it opened showing nothing to do.

   Two of the four tiles counted what the filter chips directly beneath them
   already selected; the other two were about STREAMS, on the screen for clips.
   So the numbers moved to the control that uses them — counts onto the chips
   you press, stream context onto the title line — and the tile row and the
   duplicate heading went. 365px -> 231px, and 0 -> 3 visible clips at
   1366x700.

2. A PYTHON REPR WAS ON SCREEN. Settings -> Usage stats -> Top signal rendered
   "SignalType.CHAT_VELOCITY" to real users, because signals are persisted as
   the stringified enum and that endpoint passed the stored value straight
   through. It had gone unnoticed because the one other place that shows a
   signal type carried its own private `.replace('SignalType.','')`, so
   everywhere that looked right looked right for a reason that did not
   generalise.
"""

import re
from pathlib import Path

import pytest

SRC = Path("src/dashboard/aurora_html.py").read_text()
CSS = SRC.split('<script type="text/babel">')[0]
JS = SRC.split('<script type="text/babel">')[1]


def _stats_for(api, uid):
    """Call the /stats endpoint the way the dashboard does.

    asyncio.run, NOT get_event_loop().run_until_complete: the latter borrows
    whatever ambient loop the session has, and leaving that loop in a different
    state than it was found in broke an unrelated test in the same run while
    these two passed on their own."""
    import asyncio

    class _Req:
        session = {"user_id": uid}
    return asyncio.run(api.get_stats(_Req()))


def _review_screen() -> str:
    m = re.search(r"function ReviewScreen\(.*?\n\}\n\n", JS, re.S)
    assert m, "ReviewScreen not found"
    return m.group(0)


# ── 1. the chrome above the first clip ───────────────────────────────────────

def test_the_screen_name_is_not_printed_twice():
    """The page header already renders it from HEAD, with a subtitle. A second
    <h2> saying the same words 120px below cost a whole row of the viewport and
    told the reader nothing they had not just read."""
    body = _review_screen()
    assert "<h2>Clip review</h2>" not in body, \
        "the duplicate heading is back under the page header"
    assert re.search(r"review:\['Clip Review'", JS), \
        "the page header no longer names the screen either — now it has no title"


def test_the_stat_tile_row_is_gone():
    """Four tiles, 130px, two of them duplicating the chips below and two of
    them about streams on the screen for clips."""
    assert "rd-stats" not in SRC, "the stat tile row is back above the clips"
    assert "RdStat" not in JS, "the stat tile component is back"


def test_no_dead_stat_tile_styling_was_left_behind():
    """Removing the markup and leaving the rules is how a stylesheet turns into
    a graveyard nobody dares touch."""
    leftovers = [m.group(0) for m in re.finditer(r"\.rd-stat[.\s{:][^{]*\{", CSS)
                 if not m.group(0).startswith(".rd-status")]
    assert not leftovers, f"dead stat-tile CSS: {leftovers}"


def test_the_counts_moved_onto_the_chips_that_select_them():
    """"Pending review 26" as a tile and the Pending chip were the same fact
    130px apart, and only one of them did anything when you pressed it."""
    body = _review_screen()
    m = re.search(r"\[\['all', clipsArr\.length\], \['pending', pending\], "
                  r"\['approved', approved\]\]", body)
    assert m, "the filter chips no longer carry their counts"
    assert "rd-filter-n" in body, "the count has no element to render into"
    assert ".rd-filter-n{" in CSS, "the chip count is unstyled"


def test_the_chip_counts_cannot_make_the_controls_jump():
    """Clips arrive over the socket while you are aiming at a chip. Proportional
    figures would resize the row under the cursor every time one landed."""
    m = re.search(r"\.rd-filter-n\{([^}]*)\}", CSS)
    assert m and "tabular-nums" in m.group(1), \
        "chip counts are not tabular — the row will shuffle as clips arrive"


def test_the_stream_context_is_only_shown_when_there_is_any():
    """"0 live · avg trigger 0" is four words for "nothing is happening", and it
    was half the tile row. It appears when there is something to report."""
    body = _review_screen()
    assert re.search(r"\{streamsArr\.length > 0 &&\s*\n\s*<span className=\"rd-toolbar-meta\"",
                     body), "the stream line renders even with no streams"
    assert re.search(r"\{avgScore > 0 && <>", body), \
        "an average of zero is still printed"


def test_the_toolbar_still_says_how_many_clips_are_shown():
    """The count was the one thing on that line worth keeping."""
    body = _review_screen()
    assert "rd-toolbar-count" in body
    assert "shown.length === clipsArr.length" in body, \
        "the 'N of M' form was lost — a filtered view would claim to be everything"


def test_the_count_reads_as_the_heading_now_that_it_is_first():
    """It used to sit next to an <h2> as 12px grey supporting text. With the
    heading gone it is the first thing on the line and has to carry it."""
    m = re.search(r"\.rd-toolbar-count\{([^}]*)\}", CSS)
    assert m, ".rd-toolbar-count rule not found"
    size = re.search(r"font-size:([\d.]+)px", m.group(1))
    assert size and float(size.group(1)) >= 14, \
        "the count is still styled as supporting text under a heading that is gone"


# ── 2. the Python repr on screen ─────────────────────────────────────────────

def test_a_stored_enum_string_never_reaches_the_user():
    from src.trigger.signals import signal_label
    assert signal_label("SignalType.CHAT_VELOCITY") == "Chat velocity"


@pytest.mark.parametrize("stored", [
    "SignalType.KEYWORD",   # what the enum stringifies to
    "KEYWORD",              # what a bare name looks like
    "keyword",              # the enum's own VALUE
])
def test_every_shape_a_signal_has_ever_been_stored_as_is_understood(stored):
    """Clips on disk predate any one of these being settled on, and a clip from
    last year must not print a repr."""
    from src.trigger.signals import signal_label
    assert signal_label(stored) == "Keyword hits", stored


def test_an_unnamed_signal_is_still_given_a_name():
    """Returning "" would silently blank the field, and returning the raw value
    is the bug. A signal we have not named yet is worth naming badly."""
    from src.trigger.signals import signal_label
    got = signal_label("SignalType.SOMETHING_NEW")
    assert "SignalType" not in got
    assert "_" not in got
    assert got == "Something new"


def test_nothing_is_returned_for_nothing():
    from src.trigger.signals import signal_label
    assert signal_label("") == ""
    assert signal_label(None) == ""


def test_every_signal_the_engine_can_emit_has_a_label():
    """A new SignalType added without a label would reach the UI as a shouty
    upper-case fallback — better than a repr, still not a name."""
    from src.trigger.signals import SIGNAL_LABELS, SignalType
    missing = [s.name for s in SignalType if s.name not in SIGNAL_LABELS]
    assert not missing, f"SignalType members with no human label: {missing}"


def test_the_frontend_and_the_backend_do_not_disagree_about_the_names():
    """The two runtimes each need their own copy, so a test has to hold them
    together — nothing else can."""
    from src.trigger.signals import SIGNAL_LABELS
    m = re.search(r"const SIGNAL_LABELS = \{(.*?)\n\};", JS, re.S)
    assert m, "the frontend label table was not found"
    front = dict(re.findall(r"(\w+):\s*'([^']+)'", m.group(1)))
    assert front, "the frontend label table parsed empty"
    assert front == SIGNAL_LABELS, (
        "the two label tables disagree:\n"
        f"  only in the browser: { {k: v for k, v in front.items() if SIGNAL_LABELS.get(k) != v} }\n"
        f"  only on the server:  { {k: v for k, v in SIGNAL_LABELS.items() if front.get(k) != v} }")


def test_there_is_only_one_signal_label_table_in_the_browser():
    """There were two, and they disagreed: the clip modal called KEYWORD
    "Keyword hits" and the streams panel called the same signal "Keyword". One
    product, one signal, two names, depending which screen you stood on."""
    inline = re.findall(r"\['([A-Z_]+)','([^']+)'\]", JS)
    assert not inline, (
        "a screen is naming signals inline again instead of using signalLabel(): "
        + str(sorted({k for k, _ in inline})))
    assert JS.count("const SIGNAL_LABELS") == 1


def test_each_screen_still_chooses_which_signals_it_lists():
    """Sharing the NAMES must not have silently changed which rows appear: the
    modal shows the four the formula scores on, the streams panel shows every
    weight the learner has touched."""
    assert "const sigKeys = ['CHAT_VELOCITY','KEYWORD','SENTIMENT','AUDIO_SPIKE'];" in JS
    assert "const WK=['CHAT_VELOCITY','KEYWORD','SENTIMENT','AUDIO_SPIKE'," \
           "'VIEWER_SPIKE','SILENCE_BURST'];" in JS


def test_the_usage_stats_endpoint_labels_the_signal_it_returns(monkeypatch, tmp_path):
    """The end-to-end version: the value this endpoint puts in the payload is
    what Settings renders, and it is the only thing that knows the stored form."""
    import time
    from src.dashboard import api

    monkeypatch.setattr(api, "_clips", {
        "c1": {"id": "c1", "user_id": "u1", "channel": "nova", "status": "approved",
               "created_at": time.time(), "trigger_score": 70, "virality_score": 60,
               "trigger_signals": [{"type": "SignalType.CHAT_VELOCITY", "value": 0.9},
                                   {"type": "SignalType.KEYWORD", "value": 0.2}]},
    })
    rows = _stats_for(api, "u1")
    assert len(rows) == 1
    assert rows[0]["top_signal"] == "Chat velocity"
    assert "SignalType" not in str(rows)


def test_a_channel_with_no_signals_says_so_rather_than_going_blank(monkeypatch):
    import time
    from src.dashboard import api
    monkeypatch.setattr(api, "_clips", {
        "c1": {"id": "c1", "user_id": "u1", "channel": "nova", "status": "pending",
               "created_at": time.time(), "trigger_score": 0, "virality_score": 0,
               "trigger_signals": []},
    })
    assert _stats_for(api, "u1")[0]["top_signal"] == "—"


def test_the_label_is_applied_at_the_boundary_not_in_the_template():
    """The modal's private `.replace('SignalType.','')` is exactly why this was
    invisible: the one screen that looked right looked right for a reason that
    did not generalise to any other."""
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.get_stats)
    assert "signal_label(" in src, \
        "the endpoint hands out the raw stored value again"
