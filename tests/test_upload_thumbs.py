"""The Clip Editor's filmstrip, cut on the server (owner, 2026-09-24).

"The bottom bar on the clip editor is just a black screen. Nothing renders I
need this fixed." The strip was drawn in the browser from a hidden,
hardware-decoded video; it now comes from ffmpeg on the server, with the
browser way kept as a fallback that never paints an undecoded frame black.
"""

import asyncio
import json

import pytest

from src.uploads import thumbs as T


def test_sixteen_points_at_each_tiles_middle():
    ts = T.times(58.0)
    assert len(ts) == T.THUMB_N == 16
    assert ts[0] == pytest.approx(58.0 / 32) and ts[-1] < 58.0


def test_each_frame_is_a_fast_seek_and_a_small_cover_crop():
    cmd = T.command(__import__("pathlib").Path("/x/a.mp4"), 12.5)
    assert cmd.index("-ss") < cmd.index("-i"), "must seek by keyframe, not decode from 0"
    assert cmd[cmd.index("-threads") + 1] == "1"
    assert "scale=128:72:force_original_aspect_ratio=increase,crop=128:72" in cmd


def _fake_ffmpeg(monkeypatch, calls, fail_at=()):
    class Proc:
        def __init__(self, args):
            self.args = args
            self.returncode = 0
        async def communicate(self):
            if self.args[0] == "ffprobe":
                return b"58.0\n", b""
            calls.append(float(self.args[self.args.index("-ss") + 1]))
            if len(calls) - 1 in fail_at:
                self.returncode = 1
                return b"", b""
            return b"\xff\xd8jpeg\xff\xd9", b""

    async def fake_exec(*args, **kw):
        return Proc(args)
    monkeypatch.setattr(T.asyncio, "create_subprocess_exec", fake_exec)


def test_the_strip_is_cut_one_frame_at_a_time_and_cached(monkeypatch, tmp_path):
    src = tmp_path / "a.mp4"; src.write_bytes(b"x")
    calls = []
    _fake_ffmpeg(monkeypatch, calls, fail_at=(3,))
    out = asyncio.run(T.strip(src))
    assert len(out) == 16 and len(calls) == 16
    assert out[0].startswith("data:image/jpeg;base64,")
    assert out[3] == "", "a frame that could not be cut is left plain, not black"
    assert T.cache_path(src).exists()
    calls.clear()
    assert asyncio.run(T.strip(src)) == out
    assert calls == [], "the cache was not used"


def test_the_endpoint_is_the_owners_and_the_sidecar_goes_with_the_upload(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.uploads import library as lib
    people = {"a": {"id": "a", "is_admin": True}, "b": {"id": "b", "is_admin": True}}
    monkeypatch.setattr(user_store, "get_by_id", lambda uid: people.get(uid))
    monkeypatch.setattr(api.settings, "uploads_enabled", True)
    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    lib.load()

    async def chunks():
        yield b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64
    up = asyncio.run(lib.save_stream("a", "clip.mp4", chunks()))

    async def fake_strip(path):
        T.cache_path(path).write_text(json.dumps({"thumbs": ["data:x"] * 16}))
        return ["data:x"] * 16
    monkeypatch.setattr(T, "strip", fake_strip)
    c = TestClient(api.app, base_url="https://testserver")

    def login(uid):
        from itsdangerous import TimestampSigner
        import base64
        c.cookies.clear()
        s = TimestampSigner(api.settings.dashboard_secret_key)
        c.cookies.set("session", s.sign(base64.b64encode(json.dumps(
            {"auth": True, "user_id": uid, "subscription_status": "active"}).encode())).decode())
        return c
    r = login("a").get(f"/uploads/{up.id}/thumbs")
    assert r.status_code == 200 and len(r.json()["thumbs"]) == 16
    assert login("b").get(f"/uploads/{up.id}/thumbs").status_code == 404
    side = T.cache_path(lib.path_for(up))
    assert side.exists()
    lib.delete(up.id, "a")
    assert not side.exists(), "frames of a deleted video were left on disk"
    lib._uploads.clear()


def test_the_editor_asks_the_server_first_and_never_paints_an_empty_frame():
    from src.dashboard.aurora_html import DASHBOARD_HTML as page
    a = page.index("function ClipEditor(")
    ed = page[a:page.index("/* ── Scheduler", a)]
    assert "fetch('/uploads/' + clip.id + '/thumbs')" in ed
    body = page[page.index("function buildThumbs("):page.index("function outputSize(")]
    assert "document.body.appendChild(tv)" in body, "the fallback's video is not on the page"
    assert "if (tv.readyState < 2) continue;" in body, "an undecoded frame is still drawn as black"
    assert "tv.remove();" in body
    assert "const THUMB_N = 16;" in page and T.THUMB_N == 16
