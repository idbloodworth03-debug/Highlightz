"""Edited clips show in the Clip Library (owner, 2026-11-21: "Edited posts
dont show up in the clip library we need to make it show in the clip
library").

An edit is a render in the uploads library (source "render"): the editor's
export and Autopilot both write one. The Clip Library listed approved clips
only, so an edit was visible in the Clip Editor's list and the Scheduler and
nowhere else. Now the library has an Edited shelf: every render, newest first,
playable, with Edit and Download, and where it was posted from its Scheduler
item.

Pinned: the status line read from a Scheduler item (run in node), the
realtime wiring (mount, reconnect, upload_added / upload_removed), the App
handing the screen what it needs, and that the server really marks renders
"render" in GET /uploads, which is what the shelf filters on.
"""

import io
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard import api

SRC = Path("src/dashboard/aurora_html.py").read_text()
JS = SRC.split('<script type="text/babel">')[1]
NODE = shutil.which("node") or "/opt/node22/bin/node"
needs_node = pytest.mark.skipif(not Path(NODE).exists(), reason="node is not installed here")


def _status(item):
    names = re.search(r"\nconst PITCH_NAMES = [^\n]*\n", JS)
    fn = re.search(r"\nfunction editStatus\(item\) \{.*?\n\}\n", JS, re.S)
    assert names and fn, "editStatus or PITCH_NAMES moved: this test no longer tests anything"
    prog = names.group(0) + fn.group(0) + f"\nconsole.log(JSON.stringify(editStatus({json.dumps(item)})));"
    out = subprocess.run([NODE, "-e", prog], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_a_posted_edit_says_where_and_links_to_the_post():
    tags = _status({"status": "posted", "scheduled": True, "due_at": 1,
                    "results": {"tiktok": {"status": "posted", "url": "https://www.tiktok.com/@a/video/1"},
                                "youtube": {"status": "posted", "url": ""}}})
    assert {"cls": "ok", "text": "Posted to TikTok", "href": "https://www.tiktok.com/@a/video/1"} in tags
    assert {"cls": "ok", "text": "Posted to YouTube", "href": ""} in tags
    assert len(tags) == 2


@needs_node
def test_a_failed_post_says_so_beside_what_did_land():
    tags = _status({"status": "failed", "results": {"tiktok": {"status": "failed", "error": "x"},
                                                    "youtube": {"status": "posted", "url": "u"}}})
    assert [t["text"] for t in tags] == ["Posted to YouTube", "Post failed"]


@needs_node
def test_waiting_edits_say_scheduled_or_in_the_scheduler():
    assert _status({"status": "pending", "scheduled": False, "due_at": 0, "results": {}}) == \
        [{"cls": "wait", "text": "In the Scheduler"}]
    tags = _status({"status": "pending", "scheduled": True, "due_at": 1893456000, "results": {}})
    assert len(tags) == 1 and tags[0]["text"].startswith("Scheduled ")
    assert _status({"status": "posting", "results": {}}) == [{"cls": "wait", "text": "Posting…"}]


@needs_node
def test_an_edit_never_queued_has_no_status_line():
    assert _status(None) == []


def _fn(name):
    m = re.search(r"\nfunction " + name + r"\(.*?\n\}\n", JS, re.S)
    assert m, name + " moved"
    return m.group(0)


def test_the_shelf_loads_on_mount_and_on_every_reconnect():
    hook = _fn("useEdits")
    assert "fetch('/uploads')" in hook
    assert "u.source==='render'" in hook
    assert "addEventListener('hz_refetch', load)" in hook
    assert "removeEventListener('hz_refetch', load)" in hook


def test_the_shelf_follows_other_tabs_live():
    hook = _fn("useEdits")
    assert "m.event==='upload_added'" in hook and "m.upload.source==='render'" in hook
    assert "m.event==='upload_removed'" in hook
    assert "addEventListener('hz_ws', onWs)" in hook
    # and the App forwards those two events to hz_ws at all
    assert re.search(r"\['upload_added','upload_removed'\]\.includes\(msg\.event\)\)\{\s*"
                     r"window\.dispatchEvent\(new CustomEvent\('hz_ws'", JS)


def test_it_only_asks_for_accounts_that_can_open_the_editor():
    hook = _fn("useEdits")
    assert "if(!on){ setEdits([]); return; }" in hook
    lib = _fn("LibraryScreen")
    # uploadsOn is the release switch; editorOn is whether THIS account's plan
    # has the editor (audit, 2026-11-24: Free got a 403 from /uploads on
    # every load of the library).
    assert "editorOn = uploadsOn" in lib
    assert "const edits = useEdits(uploadsOn && editorOn);" in lib
    assert "<EditedShelf edits={edits} queue={queue} onEdit={onEditUpload}/>" in lib


def test_the_app_hands_the_library_what_the_shelf_needs():
    line = next(l for l in JS.splitlines() if "view==='library') screen=<LibraryScreen" in l)
    nxt = JS[JS.index(line):JS.index(line) + 600]
    assert "uploadsOn,editorOn,queue,onEditUpload:clipTabOn?(u=>{ setEditorTarget(u); setRoute('uploads'); }):null" in nxt


def test_the_shelf_card_plays_edits_and_downloads_them():
    card = _fn("EditedCard")
    assert "<video src={up.url} controls playsInline preload=\"metadata\"/>" in card
    assert "href={up.url + '?download=1'}" in card


# ── the server side the filter relies on ─────────────────────────────────────

def test_an_exported_edit_is_listed_as_a_render(tmp_path, monkeypatch):
    from src.auth import users as user_store
    from src.uploads import library as lib
    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    monkeypatch.setattr(lib, "_uploads", {})
    monkeypatch.setattr(lib, "_loaded", True)
    monkeypatch.setattr(api, "_login_attempts", {})
    monkeypatch.setattr(api.settings, "uploads_enabled", True)
    user_store.create("editor_admin", "editor-admin-pw-1", is_admin=True)
    c = TestClient(api.app, base_url="https://testserver")
    assert c.post("/login", data={"password": "editor-admin-pw-1"}, follow_redirects=False).status_code == 302
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096
    r = c.post("/uploads?source=render", files={"file": ("clip-9x16.mp4", io.BytesIO(mp4), "video/mp4")})
    assert r.status_code == 201, r.text
    listed = c.get("/uploads").json()["uploads"]
    assert [u["source"] for u in listed] == ["render"]
    assert listed[0]["url"] == f"/uploads/{listed[0]['id']}/file"
    # Download is what the shelf's button asks for
    d = c.get(listed[0]["url"] + "?download=1")
    assert d.status_code == 200 and "attachment" in d.headers.get("content-disposition", "")
