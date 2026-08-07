"""Background loops, and the two ways they quietly stopped running.

Both bugs here have the same shape: a periodic task whose clock is reset by a
process restart. Nothing throws, nothing logs, and the only symptom is work
that never happens — which on a 1vCPU box with a 50GB disk shows up weeks later
as "why is the disk full" or "why is this dead stream still holding a slot".

They were only found by reading the loops, so they are pinned here.
"""

import ast
import inspect
import re
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src"


def _loop_body(func) -> ast.While:
    """The `while True:` node inside a function, so the assertions below are
    about structure rather than about a substring that could move."""
    tree = ast.parse(inspect.getsource(func).lstrip())
    for node in ast.walk(tree):
        if isinstance(node, ast.While) and isinstance(node.test, ast.Constant) \
                and node.test.value is True:
            return node
    raise AssertionError(f"{func.__name__} has no `while True:` loop")


def _is_sleep(stmt) -> bool:
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Await)
            and isinstance(stmt.value.value, ast.Call)
            and getattr(stmt.value.value.func, "attr", "") == "sleep")


# ── the disk sweep ───────────────────────────────────────────────────────────

def test_the_clip_sweep_does_its_work_before_it_sleeps():
    """It used to `await asyncio.sleep(86400)` as the FIRST statement of the
    loop, so it only ever ran on a process that had been up for 24 uninterrupted
    hours. Deploy more than once a day — which is every active week — and the
    30-day clip cleanup never executed at all, on a box with a 50GB disk.
    """
    from src.main import auto_delete_old_clips
    loop = _loop_body(auto_delete_old_clips)
    assert not _is_sleep(loop.body[0]), \
        "the sweep sleeps before its first pass again — it will never run between deploys"


def test_the_clip_sweep_still_sleeps_somewhere():
    """The obvious way to break the fix above: move the sleep out and forget to
    put it back, turning a daily task into a busy loop on a shared vCPU. This
    happened while making the change."""
    from src.main import auto_delete_old_clips
    loop = _loop_body(auto_delete_old_clips)
    assert any(_is_sleep(s) for s in loop.body), \
        "the loop has no sleep at all — it would spin the CPU"
    last = loop.body[-1]
    assert _is_sleep(last), "the sleep should close the loop body"


def test_the_sweep_settles_before_the_first_pass():
    """Running flat-out at t=0 competes with restoring every stream worker."""
    src = inspect.getsource(__import__("src.main", fromlist=["x"]).auto_delete_old_clips)
    pre = src.split("while True:")[0]
    assert re.search(r"await asyncio\.sleep\(\d+\)", pre), \
        "no settle delay before the first sweep"


# ── the idle clock ───────────────────────────────────────────────────────────

def test_the_idle_clock_survives_a_restart(tmp_path, monkeypatch):
    """_user_last_active was memory-only, and the reaper reads a MISSING entry
    as "active right now". So every restart wiped the clock and handed every
    abandoned stream another full 8 hours — during frequent deploys the reaper
    never fired and dead streams held their slot against the process-wide
    capacity limit indefinitely.
    """
    from src.dashboard import api
    monkeypatch.setattr(api, "_ACTIVITY_FILE", tmp_path / "activity.json")
    before = dict(api._user_last_active)
    try:
        api._user_last_active.clear()
        api._user_last_active["ghost"] = 1000.0
        api._save_activity()
        assert (tmp_path / "activity.json").exists(), "the clock was never written"
        # A restart re-reads it rather than starting empty.
        assert api._load_activity() == {"ghost": 1000.0}
    finally:
        api._user_last_active.clear()
        api._user_last_active.update(before)


def test_a_corrupt_or_missing_activity_file_is_not_fatal(tmp_path, monkeypatch):
    """Bookkeeping must never stop the app from booting."""
    from src.dashboard import api
    monkeypatch.setattr(api, "_ACTIVITY_FILE", tmp_path / "nope.json")
    assert api._load_activity() == {}
    (tmp_path / "bad.json").write_text("{not json")
    monkeypatch.setattr(api, "_ACTIVITY_FILE", tmp_path / "bad.json")
    assert api._load_activity() == {}


def test_a_user_with_no_activity_record_falls_back_to_when_they_added_the_stream():
    """The other half. On the first boot after this shipped there is no file,
    so every user is missing — and defaulting to `now` is exactly the bug. The
    fallback has to be a time in the PAST, or a stream abandoned months ago
    still looks like the user was just here.
    """
    src = inspect.getsource(
        __import__("src.dashboard.api", fromlist=["x"]).idle_stream_reaper)
    body = re.sub(r"#.*", "", src)
    assert "_user_last_active.get(uid, now)" not in body, \
        "a missing activity record defaults to now again"
    assert "added_at" in body, \
        "there is no past-dated fallback for a user with no activity record"


def test_the_reaper_persists_the_clock_on_its_own_tick():
    """Writing on every request would be far too chatty; the reaper already
    wakes every 5 minutes, so it carries the write."""
    src = inspect.getsource(
        __import__("src.dashboard.api", fromlist=["x"]).idle_stream_reaper)
    assert "_save_activity()" in src, "the clock is never persisted"


# ── the escapes ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("module", ["aurora_html", "api"])
def test_no_embedded_page_carries_an_invalid_python_escape(module):
    """Every page in these modules is a Python triple-quoted string, so Python
    resolves escapes BEFORE the browser sees them. A JS regex literal written
    here needs a backslash that is not a valid Python escape: today that is a
    DeprecationWarning and the character passes through, 3.12 raised it to a
    SyntaxWarning, and it is slated to become a SyntaxError — at which point the
    module stops importing and the app does not boot at all.

    Compiling the file with warnings promoted is the honest check: it catches
    the escape wherever it is, including in code that is not a page constant.
    """
    import py_compile
    import warnings

    path = SRC / "dashboard" / f"{module}.py"
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        py_compile.compile(str(path), doraise=True, cfile="/tmp/_esc_check.pyc")
    bad = [str(w.message) for w in caught if "invalid escape" in str(w.message)]
    assert not bad, f"{module}.py has invalid escape sequences: {bad}"


def test_the_thumbnail_and_extension_regexes_still_exist():
    """The fix replaced two regex literals with RegExp(); a later edit that
    deletes them rather than porting them would break high-res thumbnails and
    export filenames silently."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as html
    assert "-preview-[0-9]+x[0-9]+[.]" in html, "the hi-res thumbnail regex is gone"
    assert "[.][^.]+$" in html, "the extension-stripping regex is gone"
