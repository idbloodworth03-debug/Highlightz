"""The dashboard on an iPhone (luclips, 2026-10-21).

WHAT HAPPENED. Chrome on iOS 26.6: a black screen, by the user's account,
and 35 loads of the dashboard in the log window, mostly right after a video
was opened. One export's save to the server came back 400. The journal could
say nothing more, because nothing in the page reported anything. Two
mechanisms fit: iOS killing the tab for memory, and the tab being sent to a
bare video file. Everything below addresses one or both; neither is proven
on his phone.

WHAT CHANGED, each pinned below:
  * Download buttons get an attachment, not the bare video in the tab
    (server half in test_uploads.py).
  * An export on an iPhone is not handed to a script-clicked blob download;
    Save video opens the share sheet from a tap, with the server's copy as
    the fallback.
  * Phones export the 720 class at 30 fps: under half the pixels, half the
    frames, and the export holds the whole file in memory while it writes it.
  * A reload comes back to the section it left (never into the editor).
  * A screen that crashes shows a way out instead of a blank page, and the
    page reports what broke (server half in test_client_errors.py).
  * Approve and Reject send one request per clip at a time: the journal had
    one reject four times in a second.

WHY NODE. Most of this is behaviour (what a stored route resolves to, which
messages are noise, what a tap on Save video does when the share sheet cannot
take a file), so the real code is cut out of aurora_html.py and run. Where it
needs React to run, it is checked in Chromium instead (the visual harness);
here, its wiring is pinned.
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

needs_node = pytest.mark.skipif(not Path(NODE).exists(), reason="node is not installed here")


def _node(code: str):
    out = subprocess.run([NODE, "-e", textwrap.dedent(code)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _cut(src: str, start: str, end: str) -> str:
    i = src.index(start)
    return src[i:src.index(end, i) + len(end)]


# ── crash reports: the plain script ahead of React ──────────────────────────

def _reporter() -> str:
    m = re.search(r"<script>(.*?)</script>", SRC, re.S)
    assert m and "window.hzReport" in m.group(1), "the crash reporter moved"
    return m.group(1)


def test_the_reporter_runs_before_react_and_babel_so_a_page_that_never_starts_still_reports():
    at = SRC.index("window.hzReport = function")
    assert at < SRC.index("react.production.min.js"), "the reporter loads after React"
    assert at < SRC.index('<script type="text/babel">'), "the reporter is inside the Babel block"


@needs_node
def test_the_reporter_sends_each_message_once_and_at_most_eight_a_page():
    got = _node("""
    const calls = [], listeners = {};
    const window = { addEventListener: (t, f) => { listeners[t] = f; }, hzWhere: 'uploads' };
    const fetch = (url, opts) => { calls.push([url, opts]); return Promise.resolve(); };
    (function (window, fetch) { %s })(window, fetch);
    window.hzReport('render', 'boom', 'at UploadScreen', 'uploads');
    window.hzReport('render', 'boom', 'at UploadScreen', 'uploads');      // the same: once
    window.hzReport('error', 'boom', '');                                  // another kind: its own
    for (let i = 0; i < 20; i++) window.hzReport('error', 'distinct ' + i, '');
    console.log(JSON.stringify({
      n: calls.length,
      url: calls[0][0], method: calls[0][1].method, keepalive: calls[0][1].keepalive,
      body: JSON.parse(calls[0][1].body),
      second: JSON.parse(calls[1][1].body),
    }));
    """ % _reporter())
    assert got["n"] == 8, "the per-page cap is gone"
    assert (got["url"], got["method"], got["keepalive"]) == ("/client-error", "POST", True)
    assert got["body"] == {"kind": "render", "message": "boom", "stack": "at UploadScreen",
                           "where": "uploads"}
    # No `where` given: the section the app last recorded.
    assert got["second"]["kind"] == "error" and got["second"]["where"] == "uploads"


@needs_node
def test_what_the_page_cannot_act_on_is_not_reported():
    """Every deploy restarts the server under every open tab. Reporting the
    dropped fetches and the HTML-instead-of-JSON replies from that would mail
    the owner on every deploy, and an alert that cries wolf gets ignored."""
    noise = ["Failed to fetch", "Load failed", "NetworkError when attempting to fetch resource.",
             "The network connection was lost.", "Script error.", "Script error",
             "ResizeObserver loop completed with undelivered notifications.",
             "The operation was aborted.", "AbortError: The user aborted a request.",
             "Unexpected token '<', \"<html>\" is not valid JSON",
             "The string did not match the expected pattern.",
             "JSON.parse: unexpected character at line 1 column 1 of the JSON data", ""]
    got = _node("""
    const calls = [];
    const window = { addEventListener: () => {} };
    const fetch = (u, o) => { calls.push(o); return Promise.resolve(); };
    (function (window, fetch) { %s })(window, fetch);
    for (const m of %s) window.hzReport('rejection', m, '');
    window.hzReport('render', "Cannot read properties of undefined (reading 'id')", '');
    console.log(JSON.stringify(calls.map(o => JSON.parse(o.body).message)));
    """ % (_reporter(), json.dumps(noise)))
    assert got == ["Cannot read properties of undefined (reading 'id')"]


@needs_node
def test_uncaught_errors_and_rejections_are_reported_and_a_failing_send_never_throws():
    got = _node("""
    const calls = [], listeners = {};
    const window = { addEventListener: (t, f) => { listeners[t] = f; } };
    let fail = false;
    const fetch = (u, o) => { if (fail) throw new Error('no fetch'); calls.push(JSON.parse(o.body)); return Promise.resolve(); };
    (function (window, fetch) { %s })(window, fetch);
    listeners.error({ message: 'x is not a function', error: { stack: 'TypeError: x\\n at f' } });
    listeners.unhandledrejection({ reason: new RangeError('bad length') });
    listeners.unhandledrejection({ reason: 'a plain string' });
    fail = true;
    let threw = false;
    try { window.hzReport('render', 'while fetch is broken', ''); } catch (e) { threw = true; }
    console.log(JSON.stringify({ calls, threw }));
    """ % _reporter())
    assert [(c["kind"], c["message"]) for c in got["calls"]] == [
        ("error", "x is not a function"), ("rejection", "bad length"),
        ("rejection", "a plain string")]
    assert got["calls"][0]["stack"].startswith("TypeError: x")
    assert got["threw"] is False


# ── a crashed screen gets a way out ─────────────────────────────────────────

def test_a_crashed_screen_is_caught_reported_and_offers_a_way_out():
    eb = _cut(JS, "class ErrorBoundary extends React.Component {", "\n}\n")
    assert "static getDerivedStateFromError(err)" in eb
    assert "window.hzReport('render'" in eb and "info.componentStack" in eb
    assert "this.setState({ err: null })" in eb, "no Try again"
    assert "onHome()" in eb
    assert "reload" not in eb.lower(), "the way out must not be a page reload"


def test_the_screen_boundary_is_keyed_by_section_so_the_nav_still_works():
    main = _cut(JS, "<main className={'rd-screen'", "</main>")
    assert "<ErrorBoundary key={view} where={view}" in main
    assert "{screen}</ErrorBoundary>" in main
    assert "onHome={view!=='review' ? ()=>setRoute('review') : null}" in main
    # The nav is outside it, so a broken screen leaves every other one reachable.
    assert JS.index('<nav className=') < JS.index("<main className={'rd-screen'")


def test_the_whole_app_is_wrapped_too():
    assert "render(<ErrorBoundary where=\"app\"><RdApp/></ErrorBoundary>);" in JS
    assert "useEffect(()=>{ window.hzWhere = view; }, [view]);" in JS


# ── the section survives a reload ───────────────────────────────────────────

def _saved_route(stored, throws=False):
    head = _cut(JS, "const HEAD={", "};")
    fn = _cut(JS, "function savedRoute() {", "\n}\n")
    return _node("""
    %s
    function run(sessionStorage) { %s return savedRoute(); }
    const store = { getItem: (k) => { if (%s) throw new Error('blocked'); return k === 'hz_route' ? %s : null; } };
    console.log(JSON.stringify(run(store)));
    """ % (head, fn, "true" if throws else "false", json.dumps(stored)))


@needs_node
@pytest.mark.parametrize("stored, expected", [
    ("uploads", "uploads"), ("schedule", "schedule"), ("settings", "settings"),
    (None, "review"), ("", "review"), ("no-such-screen", "review"),
    # Inherited names are not routes: the header would read HEAD[route][0].
    ("__proto__", "review"), ("constructor", "review"), ("toString", "review"),
])
def test_a_reload_comes_back_to_a_real_section_and_nothing_else(stored, expected):
    assert _saved_route(stored) == expected


@needs_node
def test_blocked_storage_falls_back_to_clip_review():
    """Private mode and blocked site data throw on access; the app still opens."""
    assert _saved_route("uploads", throws=True) == "review"


def test_the_route_is_restored_and_saved_but_the_editor_is_not():
    app = _cut(JS, "function RdApp() {", "\n}\n")
    assert "const [route, setRoute] = useState(savedRoute);" in app
    assert "useEffect(()=>{ try { sessionStorage.setItem('hz_route', route); } catch (e) {} }, [route]);" in app
    # If the editor is what ran the phone out of memory, reopening it on load
    # would reload the tab again. Only the section is remembered.
    assert JS.count("sessionStorage.setItem(") == 1


# ── phones: who gets the light export, and what it is ───────────────────────

@needs_node
@pytest.mark.parametrize("ua, platform, touch, ios, light", [
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/155.0 Mobile/15E148 Safari/604.1",
     "iPhone", 5, True, True),
    ("Mozilla/5.0 (iPhone; CPU iPhone OS 26_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6 Mobile/15E148 Safari/604.1",
     "iPhone", 5, True, True),
    # iPadOS asks for the desktop site and calls itself a Mac.
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.6 Safari/605.1.15",
     "MacIntel", 5, True, True),
    ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
     "MacIntel", 0, False, False),
    ("Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Mobile Safari/537.36",
     "Linux armv8l", 5, False, True),
    ("Mozilla/5.0 (Linux; Android 15; SM-X910) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
     "Linux armv8l", 5, False, False),
    ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36",
     "Win32", 0, False, False),
])
def test_who_is_an_iphone_and_who_gets_the_light_export(ua, platform, touch, ios, light):
    m = re.search(r"const IS_IOS = .*?\nconst LIGHT_EXPORT = [^\n]*\n", JS, re.S)
    assert m, "the phone detection moved"
    consts = m.group(0)
    got = _node("""
    function run(navigator) { %s; return { IS_IOS, LIGHT_EXPORT }; }
    console.log(JSON.stringify(run(%s)));
    """ % (consts, json.dumps({"userAgent": ua, "platform": platform, "maxTouchPoints": touch})))
    assert got == {"IS_IOS": ios, "LIGHT_EXPORT": light}


def _output_size(*args):
    ratios = _cut(JS, "const RATIOS = [", "];")
    fn = _cut(JS, "function outputSize(", "\n}\n")
    return _node("%s\n%s\nconsole.log(JSON.stringify(outputSize(%s)));"
                 % (ratios, fn, ", ".join(json.dumps(a) for a in args)))


@needs_node
@pytest.mark.parametrize("ratio, light, expected", [
    ("9:16", False, {"w": 1080, "h": 1920, "hd": True}),
    ("9:16", True,  {"w": 720,  "h": 1280, "hd": False}),
    ("1:1",  True,  {"w": 720,  "h": 720,  "hd": False}),
    ("16:9", True,  {"w": 1280, "h": 720,  "hd": False}),
    ("16:9", False, {"w": 1920, "h": 1080, "hd": True}),
])
def test_a_phone_exports_the_720_class_from_a_1080p_source(ratio, light, expected):
    assert _output_size(ratio, 1920, 1080, light) == expected


@needs_node
def test_a_desktop_export_is_exactly_what_it_was():
    """The light profile is opt-in: the old three-argument call is unchanged."""
    assert _output_size("9:16", 1920, 1080) == {"w": 1080, "h": 1920, "hd": True}
    assert _output_size("9:16", 854, 480) == {"w": 720, "h": 1280, "hd": False}


def test_the_editor_hands_the_phone_flag_to_every_size_decision():
    """`hd` is what picks 60 fps and 16 Mbps on both export paths, so a
    second outputSize call without the flag would put them back on a phone."""
    ed = _cut(JS, "function ClipEditor(", "\nfunction ")
    assert ed.count("outputSize(") == 1
    assert "const out = outputSize(ratio, srcDims[0], srcDims[1], LIGHT_EXPORT);" in ed
    assert "const hd = out.hd;" in ed, "the recorder path decides HD on its own again"
    assert "fps: out.hd ? 60 : 30, hd: out.hd" in ed


# ── an export on an iPhone ──────────────────────────────────────────────────

def _run_export() -> str:
    return _cut(JS, "const runExport = async () => {", "\n  };\n")


def test_an_iphone_export_is_not_handed_to_a_blob_download():
    run = _run_export()
    assert "if (!IS_IOS) download(blob, ext);" in run
    assert run.count("download(") == 1, "another download() call in the export path"
    # It still goes to the server, and the server's id is kept for Save video.
    assert "fetch('/uploads?source=render'" in run
    assert "setOutFile(f => (f && f.blob === blob) ? { ...f, id: saved.id } : f);" in run
    # Nothing on an iPhone says the file is in the downloads folder.
    assert "(IS_IOS ? 'Exported' : 'Exported to your downloads')" in run


def test_the_iphone_gets_save_video_and_the_desktop_keeps_download_again():
    ed = _cut(JS, "function ClipEditor(", "\nfunction ")
    foot = ed[ed.index("{outFile && !busy && <div className=\"ed-row\">"):]
    foot = foot[:foot.index("</div>}")]
    assert "IS_IOS" in foot and "onClick={saveToPhone}" in foot and "Save video" in foot
    assert "onClick={()=>download(outFile.blob, outFile.ext)}>Download again" in foot


def _save_to_phone(can_share, upload_id, share_error=None):
    fn = _cut(JS, "const saveToPhone = async () => {", "\n  };\n")
    return _node("""
    const log = { share: [], assign: [], download: [], err: [] };
    function make(navigator, window, outFile, setErr, download) { %s return saveToPhone; }
    const nav = {
      canShare: (d) => %s && d.files.length === 1 && d.files[0] instanceof File,
      share: async (d) => {
        log.share.push({ name: d.files[0].name, type: d.files[0].type, size: d.files[0].size });
        if (%s) { const e = new Error(%s); e.name = %s; throw e; }
      },
    };
    const win = { location: { assign: (u) => log.assign.push(u) } };
    const out = { blob: new Blob([new Uint8Array(42)], { type: 'video/mp4' }), ext: 'mp4',
                  name: 'clip-9x16.mp4', id: %s };
    const save = make(nav, win, out, (m) => { if (m) log.err.push(m); }, (b, e) => log.download.push(e));
    save().then(() => console.log(JSON.stringify(log)));
    """ % (fn, "true" if can_share else "false",
           "true" if share_error else "false",
           json.dumps(share_error[1] if share_error else ""),
           json.dumps(share_error[0] if share_error else ""),
           json.dumps(upload_id)))


@needs_node
def test_save_video_opens_the_share_sheet_with_the_exported_file():
    got = _save_to_phone(can_share=True, upload_id="u123")
    assert got["share"] == [{"name": "clip-9x16.mp4", "type": "video/mp4", "size": 42}]
    assert got["assign"] == [] and got["download"] == [] and got["err"] == []


@needs_node
def test_closing_the_share_sheet_is_not_an_error_and_a_real_failure_says_so():
    assert _save_to_phone(True, "u1", ("AbortError", "Share canceled"))["err"] == []
    err = _save_to_phone(True, "u1", ("NotAllowedError", "not allowed"))["err"]
    assert err == ["Could not open the share sheet (not allowed)."]


@needs_node
def test_without_file_sharing_the_servers_copy_downloads_as_an_attachment():
    got = _save_to_phone(can_share=False, upload_id="u123")
    assert got["share"] == [] and got["download"] == []
    assert got["assign"] == ["/uploads/u123/file?download=1"]


@needs_node
def test_with_no_server_copy_either_the_blob_download_is_the_last_resort():
    got = _save_to_phone(can_share=False, upload_id=None)
    assert got["download"] == ["mp4"] and got["assign"] == []


def test_the_share_sheet_is_opened_from_the_tap_with_nothing_awaited_first():
    """WebKit opens the sheet only inside the tap's user activation; an await
    before it (a fetch, say) can outlive that and the sheet refuses."""
    fn = _cut(JS, "const saveToPhone = async () => {", "\n  };\n")
    assert "await" not in fn[:fn.index("await navigator.share(")]


# ── every Download button to an upload is a real download ───────────────────

def test_every_link_to_an_upload_file_with_download_asks_for_an_attachment():
    links = re.findall(r"<a[^>]*?/uploads/[^>]*?/file[^>]*?>", JS, re.S)
    assert len(links) >= 3, "the upload download links moved"
    for a in links:
        assert "?download=1" in a, a


def test_no_video_takes_over_the_whole_screen_on_an_iphone():
    """Without playsinline, iOS plays a <video> in its own full-screen black
    player. Every video in the dashboard plays in the page."""
    for v in re.findall(r"<video\b[^>]*>", JS):
        assert "playsInline" in v, v


# ── approve and reject: one request per clip at a time ──────────────────────

def test_approve_and_reject_share_one_lock_per_clip():
    app = _cut(JS, "function RdApp() {", "\n}\n")
    assert "const clipBusy = useRef(new Set());" in app
    for fn in ("const approveClip = async(id)=>{", "const rejectClip = async(id)=>{"):
        body = _cut(app, fn, "\n  };\n")
        assert "if(clipBusy.current.has(id)) return" in body
        assert "clipBusy.current.add(id);" in body
        assert "finally { clipBusy.current.delete(id); }" in body
        # The check comes before the request, or it locks nothing.
        assert body.index("clipBusy.current.has(id)") < body.index("await fetch(")
