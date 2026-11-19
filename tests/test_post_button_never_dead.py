"""No Post button may do nothing when it is pressed.

Owner, 2026-11-19: "it looks like the post button in general is not working",
then: "no like the post button is literally doing nothing when I press it".

Reproduced in a browser. A queued clip's "Post now" in the Scheduler was
`disabled` whenever the server had nothing it could post by itself, and
TikTok is never one of those (its choices are the person's, every post). So
for every account whose only open platform is TikTok, which is every ordinary
account, that button was dead on every clip. And it did not LOOK dead: there
was no style at all for a disabled .rd-btn, so it kept its full colour and
its pointer. Pressing it sent nothing and changed nothing.

Held here:
  * the Scheduler's button is never switched off: with something the server
    can post it posts, otherwise it opens the Post dialog for THAT clip;
  * the dialog can post a queued clip (by its upload id), with the accounts
    already ticked on its card and its caption;
  * a switched-off button looks switched off, everywhere.
"""

import json
import re
import subprocess
from pathlib import Path

SRC = Path("src/dashboard/aurora_html.py").read_text()
CSS = SRC.split('<script type="text/babel">')[0]
JS = SRC.split('<script type="text/babel">')[1]


def _body(name: str) -> str:
    start = JS.index(f"function {name}(")
    return JS[start:JS.index("\nfunction ", start + 10)]


def test_the_schedulers_post_now_is_never_switched_off():
    drawer = _body("ScheduleDrawer")
    start = drawer.index('<button className="rd-btn sm grad" onClick={auto.length ? postNow : choosePost}')
    button = drawer[start:drawer.index("</button>", start)]
    assert "Post now" in button and "Retry" in button
    assert "disabled" not in button, "the button can be dead again"
    # It no longer sends anybody to a different screen to do the same thing,
    # and no longer promises Instagram in its hint.
    assert "Choose ' + ap.or + ' above" not in drawer
    assert "with the clip's <b>Post</b> button in the Clip Library" not in drawer
    assert "Press <b>Post now</b> below to post it to TikTok." in drawer


def test_with_nothing_to_post_by_itself_it_opens_the_dialog_for_that_clip():
    drawer = _body("ScheduleDrawer")
    choose = drawer[drawer.index("const choosePost = () => {"):drawer.index("const share = async")]
    assert "onPostNow(queuedAsClip(item, cap));" in choose and "onClose();" in choose
    # An account that cannot post at all is TOLD, on the screen, not ignored.
    assert "if (!onPostNow) { setBusyErr('Posting is not available on this account yet.'); return; }" in choose
    assert "{busyErr && <div className=\"ed-warn\">{busyErr}</div>}" in drawer
    assert "onPostNow = null })" in drawer[:300]
    screen = _body("ScheduleScreen")
    assert "onClose={()=>setOpenId(null)} onDrop={drop} onPostNow={onPostNow}/>" in screen


def test_a_queued_clip_is_handed_to_the_dialog_with_what_its_card_holds():
    fn = JS[JS.index("function queuedAsClip("):JS.index("\n}\n", JS.index("function queuedAsClip(")) + 3]
    item = {"id": "it1", "upload_id": "up9", "filename": "my export.mp4", "caption": "old words",
            "platforms": ["tiktok", "youtube"], "duration_s": 31.5, "ratio": "9:16", "fmt": "mp4"}
    code = fn + f"\nconsole.log(JSON.stringify([queuedAsClip({json.dumps(item)}, 'typed just now'), queuedAsClip({json.dumps(item)}, null), queuedAsClip({{upload_id: 'u'}}, undefined)]));"
    got = subprocess.run(["node", "-e", code], capture_output=True, text=True, timeout=30)
    assert got.returncode == 0, got.stderr
    typed, saved, bare = json.loads(got.stdout)
    assert typed == {"queued": True, "id": "", "upload_id": "up9", "channel": "", "clip_title": "my export.mp4",
                     "caption": "typed just now", "picked": ["tiktok", "youtube"], "duration_seconds": 31.5,
                     "ratio": "9:16", "fmt": "mp4", "file": "/uploads/up9/file"}
    assert saved["caption"] == "old words"                    # nothing typed since: what was saved
    assert bare["caption"] == "" and bare["picked"] == [] and bare["clip_title"] == "Your clip"


def test_the_dialog_posts_a_queued_clip_by_its_upload_id():
    dialog = _body("PostNowDialog")
    assert "...(clip.queued ? {upload_id: clip.upload_id} : {clip_id: clip.id})," in dialog
    # The accounts ticked on the card stay ticked, if they can be posted to.
    # For a CAUGHT clip nothing is ever pre-ticked, whatever fields it carries.
    assert "new Set((clip.queued ? clip.picked || [] : []).filter(id => canPostTo(conn[id])))" in dialog
    assert "clip.queued ? String(clip.caption || '')" in dialog
    # Its own video for TikTok's preview, its own shape for the fit check.
    assert "src={clip.queued ? clip.file : ('/clips/' + clip.id + '/file')}" in dialog
    assert "fitIssues(pf, clip.duration_seconds || 0, clip.ratio || '16:9', cap, clip.fmt || 'mp4')" in dialog
    # An export is not "the clip as Highlightz caught it (widescreen)".
    assert "{!sent && !clip.queued && <div className=\"sc-sub\">Posts the clip as Highlightz caught it" in dialog


def test_the_server_takes_an_upload_id_and_reuses_that_clips_card():
    import inspect
    from src.dashboard import api
    src = inspect.getsource(api.publish_post_now)
    assert 'up = upload_lib.get(str(body.get("upload_id") or ""), uid)' in src
    assert "existing = next((i for i in sched.for_user(uid) if i.upload_id == up.id" in src


def test_a_switched_off_button_looks_switched_off():
    rule = re.search(r"\.rd-btn:disabled\{([^}]*)\}", CSS)
    assert rule, "a disabled button is drawn exactly like a live one"
    assert "opacity:.5" in rule.group(1) and "cursor:default" in rule.group(1)
    # After the base rule and hover, so neither undoes it.
    assert CSS.index(".rd-btn{") < CSS.index(".rd-btn:hover{") < CSS.index(".rd-btn:disabled{")


def test_every_other_post_button_opens_the_same_dialog():
    assert "onClick={e=>{e.stopPropagation();onPost(clip)}}" in _body("RdClip")              # the clip card
    assert "onClick={()=>{onPost(clip);onClose()}}" in _body("ClipModal")                      # the clip popup
    assert "onPick={c=>{ setPostingNow(false); onPostNow && onPostNow(c); }}" in _body("ScheduleScreen")
    assert JS.count("{postClip && <PostNowDialog clip={postClip}") == 1
