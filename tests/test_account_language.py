"""The account's language decides its captions and its hook titles.

Owner, 2026-10-21: "Do the language to per-account setting. Change the hooks
to match the account language setting." From luclips' ticket: a Pro account
clipping Spanish-speaking Kick streamers got English subtitles (base.en is
English-only, so Spanish audio came out as English; confirmed on prod, both
of his caption files lang=en) and English titles on every Autopilot video
(the detector's labels, "westcol — Hype Moment").

Pinned here, end to end:
  * the setting: prefs.language, default English, through PUT /prefs and /me;
  * captions: English makes the exact call it always has; another language
    uses the multilingual model with the language forced and no English
    prompt; captions cached in another language are made again;
  * hook titles: the detector's labels in every language, on new clips (the
    stream worker) and old ones (Autopilot's title and post caption), and
    through ffmpeg's parser; the AI editor is told the language;
  * the picker in Settings, the editor's Captions panel and the Auto Edit tab.
"""

import asyncio
import base64
import inspect
import json
import re
import time

import pytest

from src import language as L

MP4 = b"\x00\x00\x00\x20ftypisom" + b"\x00" * 64


def _run(coro):
    return asyncio.run(coro)


# ── the setting ──────────────────────────────────────────────────────────────

def test_english_is_the_default_and_nothing_else_gets_through():
    from src.auth import users as user_store
    assert user_store.normalize_prefs(None)["language"] == "en", "existing accounts would change"
    assert user_store.normalize_prefs({"language": "ES"})["language"] == "es"
    for junk in ("xx", "", None, 3, "en-US", "../es"):
        assert user_store.normalize_prefs({"language": junk})["language"] == "en"
    assert L.of(None) == "en" and L.of({"prefs": {"language": "pt"}}) == "pt"


def test_every_language_is_whisper_code_with_a_name_for_the_ai_editor():
    assert list(L.LANGUAGES)[0] == "en"
    for code in L.LANGUAGES:
        assert re.fullmatch(r"[a-z]{2}", code), code
        assert L.english_name(code) and L.english_name(code).isascii()
    assert L.choices()[0] == ["en", "English"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from itsdangerous import TimestampSigner
    from src.auth import users as user_store
    from src.dashboard import api
    store = tmp_path / "users.json"
    store.write_text(json.dumps([{"id": "u1", "username": "luc", "subscription_status": "active",
                                  "plan": "pro"}]))
    monkeypatch.setattr(user_store, "_USERS_FILE", store)
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.bak.json")
    sent = []

    async def _bcast(msg, user_id=None): sent.append((msg, user_id))
    monkeypatch.setattr(api, "broadcast", _bcast)
    c = TestClient(api.app, base_url="https://testserver")
    data = base64.b64encode(json.dumps({"auth": True, "user_id": "u1", "username": "luc",
                                        "subscription_status": "active"}).encode())
    c.cookies.set("session", TimestampSigner(api.settings.dashboard_secret_key).sign(data).decode())
    c.sent = sent
    return c


def test_choosing_spanish_is_saved_and_reaches_every_open_tab(client):
    r = client.put("/prefs", json={"language": "es"})
    assert r.status_code == 200 and r.json()["language"] == "es"
    # Realtime: the existing prefs_changed event carries it, scoped to them.
    assert [(m["event"], m["prefs"]["language"], u) for m, u in client.sent] == [("prefs_changed", "es", "u1")]
    me = client.get("/me").json()
    assert me["prefs"]["language"] == "es"
    assert ["es", "Español"] in me["languages"]
    assert L.for_user_id("u1") == "es"


# ── captions ─────────────────────────────────────────────────────────────────

class _FakeModel:
    def __init__(self, name):
        self.name, self.calls = name, []

    def transcribe(self, path, **kw):
        self.calls.append(kw)
        return [], type("Info", (), {"language": kw.get("language", "en")})()


@pytest.fixture
def models(monkeypatch):
    from src.captions import transcribe as cap
    loaded = []

    def _load(name=None):
        m = _FakeModel(name or cap.settings.captions_model)
        loaded.append(m)
        return m
    monkeypatch.setattr(cap, "_load_model", _load)
    monkeypatch.setattr(cap, "_model", None)
    monkeypatch.setattr(cap, "_ml_model", None)
    return cap, loaded


def test_english_captions_are_exactly_what_they_were(models, tmp_path):
    cap, loaded = models
    cap._run_whisper(tmp_path / "x.wav")
    [m] = loaded
    assert m.name == cap.settings.captions_model == "base.en"
    assert "language" not in m.calls[0], "the English call changed"
    assert m.calls[0]["initial_prompt"] == cap.settings.captions_initial_prompt


def test_spanish_uses_the_multilingual_model_with_spanish_forced(models, tmp_path):
    """base.en given Spanish writes English — the ticket. And the default prompt
    is an English sentence, which pulls a multilingual transcript to English."""
    cap, loaded = models
    segs, lang = cap._run_whisper(tmp_path / "x.wav", language="es")
    [m] = loaded
    assert m.name == cap.settings.captions_model_multilingual == "base"
    assert m.calls[0]["language"] == "es" and m.calls[0]["initial_prompt"] is None
    assert lang == "es"
    # Both kinds stay cached; English never loads the multilingual one.
    cap._run_whisper(tmp_path / "x.wav", language="es")
    cap._run_whisper(tmp_path / "x.wav")
    assert [x.name for x in loaded] == ["base", "base.en"]


def test_a_spanish_run_records_its_language_and_model(tmp_path, monkeypatch):
    from src.captions import transcribe as cap
    seen = []
    monkeypatch.setattr(cap, "extract_audio", lambda v, out, timeout=60.0: out.write_bytes(b"RIFF"))

    def _whisper(wav, **kw):
        seen.append(kw)
        return [cap.Segment(0.0, 1.0, "qué jugada")], kw.get("language", "en")
    monkeypatch.setattr(cap, "_run_whisper", _whisper)
    video = tmp_path / "c.mp4"
    video.write_bytes(b"\x00" * 64)
    payload = _run(cap.transcribe(video, language="es"))
    assert seen == [{"language": "es"}]
    assert payload["language"] == "es" and payload["model"] == "base"
    english = _run(cap.transcribe(video))
    assert seen[-1] == {} and english["model"] == "base.en"


def test_captions_cached_in_another_language_are_made_again(tmp_path):
    from src.captions import transcribe as cap
    video = tmp_path / "c.mp4"
    cap.save(video, {"segments": [{"start": 0, "end": 1, "text": "hi"}], "language": "en"})
    assert cap.load_for(video, "en") is not None
    assert cap.load_for(video, "es") is None, "English captions were reused for a Spanish account"
    cap.save(video, {"segments": []})            # written before the language was recorded
    assert cap.load_for(video, "en") is not None and cap.load_for(video, "es") is None


def test_every_place_that_makes_captions_uses_the_accounts_language():
    from src.autopilot import auto_edit, runner
    from src.dashboard import api
    assert "load_for(path, language)" in inspect.getsource(auto_edit._transcript)
    assert "load_for(path, language)" in inspect.getsource(runner._captions_for)
    start = inspect.getsource(api.start_captions)
    assert "_language.for_user_id(uid)" in start and "language=lang" in start


# ── hook titles ──────────────────────────────────────────────────────────────

def _detector_labels():
    """Every label _generate_clip_title can produce, read from its source so a
    new English label without translations fails here."""
    from src.trigger.engine import TriggerEngine
    src = inspect.getsource(TriggerEngine._generate_clip_title)
    labels = set(TriggerEngine._SIGNAL_TITLES.values())
    labels |= set(re.findall(r'label = "([^"]+)"', src))
    labels |= set(re.findall(r'\.get\(peak, "([^"]+)"\)', src))
    assert len(labels) >= 10, labels
    return labels


def test_every_detector_label_exists_in_every_language():
    for label in _detector_labels():
        have = L._LABELS.get(label) or {}
        missing = [c for c in L.LANGUAGES if c != "en" and not have.get(c)]
        assert not missing, f"{label!r} has no {missing}"
    assert set(L._CLIP_FROM) == set(L.LANGUAGES)


def test_detector_titles_are_translated_and_nothing_else_is():
    assert L.localize_title("westcol — Hype Moment", "westcol", "es") == "westcol — Momento de hype"
    assert L.localize_title("Clip from westcol", "westcol", "es") == "Clip de westcol"
    assert L.localize_title("lacobraaa — Chat Speaks As One", "LaCobraaa", "pt") == "lacobraaa — O chat em uníssono"
    for title in ("westcol — 1:23:45",               # a VOD scan title
                  "WESTCOL EN VIVO !kick",          # a stream title
                  "westcol — Momento de hype",      # already translated
                  "nova — Hype Moment"):            # another channel's shape
        assert L.localize_title(title, "westcol", "es") == title
    assert L.localize_title("westcol — Hype Moment", "westcol", "en") == "westcol — Hype Moment"


def test_every_translated_title_survives_ffmpegs_parser():
    """The burned-in title goes through drawtext, whose escaping took the site
    down twice on an apostrophe — and French has one ("L'émotion")."""
    from tests import ffparse as F
    from src.autopilot import render as ap_render
    for label, row in L._LABELS.items():
        for lang, text in row.items():
            title = f"westcol — {text}"
            graph = ap_render.video_filter("hook", title=title, font="/f/x.ttf")
            assert [d["text"] for d in F.drawtexts(graph)] == [title], (lang, label)


def test_a_caught_clip_is_titled_in_its_accounts_language(monkeypatch):
    from src.ingestion import stream_worker
    from src.trigger.signals import TriggerEvent
    pushed = []

    class Q:
        async def push(self, job): pushed.append(job)
    monkeypatch.setattr(L, "for_user_id", lambda uid: {"u1": "es", "u2": "en"}[uid])
    for uid in ("u1", "u2"):
        w = stream_worker.StreamWorker(
            config=stream_worker.WorkerConfig(channel="westcol", platform_name="kick", user_id=uid),
            platform=None, queue=Q(), shared_buffers={})
        _run(w._on_trigger(TriggerEvent(channel="westcol", score=80.0, signals=[],
                                        clip_title="westcol — Chat Erupts")))
    assert [j.clip_title for j in pushed] == ["westcol — El chat explota", "westcol — Chat Erupts"]


def test_the_title_on_the_video_and_in_the_post_follow_the_language():
    from src import autopilot as ap
    cfg = ap.normalize({"enabled": True, "title": True, "caption_text": "{title} #{channel}"})
    old = {"channel": "westcol", "clip_title": "westcol — Loud Reaction"}   # caught in English
    assert ap.title_for(cfg, old, "es") == "westcol — Reacción a gritos"
    assert ap.caption_for(cfg, old, "es") == "westcol — Reacción a gritos #westcol"
    assert ap.title_for(cfg, old) == "westcol — Loud Reaction"
    assert ap.title_for(ap.normalize({"title": False}), old, "es") == ""


@pytest.fixture
def autopilot(tmp_path, monkeypatch):
    from src.autopilot import render as ap_render, runner
    from src.clips import files as clip_files
    from src.dashboard import api
    from src.publish import schedule as sched
    from src.uploads import library as lib
    from src.auth import users as user_store
    monkeypatch.setattr(user_store, "get_by_id",
                        lambda uid: {"id": uid, "prefs": {"language": "es"}})
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(lib, "_ROOT", tmp_path / "uploads")
    monkeypatch.setattr(lib, "_INDEX", tmp_path / "uploads.json")
    lib.load()
    monkeypatch.setattr(sched, "_INDEX", tmp_path / "schedule.json")
    sched._items.clear(); sched._loaded = False
    monkeypatch.setattr(ap_render.settings, "local_storage_path", str(tmp_path))
    monkeypatch.setattr(ap_render.settings, "captions_enabled", True)
    (tmp_path / "clipfiles").mkdir()
    monkeypatch.setattr(clip_files, "path_for", lambda cid: tmp_path / "clipfiles" / f"{cid}.mp4")
    seen = {}

    async def render(src, dst, template, **kw):
        seen["title"] = kw.get("title")
        dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(MP4)
    monkeypatch.setattr(ap_render, "render", render)

    async def captions(path, language="en"):
        seen["captions_language"] = language
        return [(0.0, 1.0, "qué jugada")]
    monkeypatch.setattr(runner, "_captions_for", captions)
    runner._inflight.clear()
    yield runner, api, sched, tmp_path, seen
    sched._items.clear(); sched._loaded = False; lib._uploads.clear()


def test_a_spanish_accounts_autopilot_video_is_in_spanish(autopilot):
    """The ticket's exact path: Luc is not early access, so the legacy edit,
    with the Hook Title style, the title on, and captions on."""
    from src import autopilot as ap
    runner, api, sched, tmp, seen = autopilot
    rec = {"id": "c1", "user_id": "u1", "status": "approved", "channel": "westcol",
           "clip_title": "westcol — Hype Moment", "duration_seconds": 30.0, "approved_at": time.time()}
    api._clips["c1"] = rec
    (tmp / "clipfiles" / "c1.mp4").write_bytes(MP4)

    async def notify(msg, uid): pass
    cfg = ap.normalize({"enabled": True, "template": "hook", "title": True, "captions": True})
    out = _run(runner.process_clip(rec, cfg, notify, connected=set()))
    assert out["status"] == "scheduled", out
    assert seen == {"title": "westcol — Momento de hype", "captions_language": "es"}
    [item] = sched._items.values()
    assert item.caption.startswith("westcol — Momento de hype")


def test_the_ai_editor_is_told_the_language(monkeypatch):
    from src.autopilot import builder, llm_common, llm_plan
    from src.autopilot import plan as P
    assert llm_common.brief([], {})["language"] == "English"
    assert llm_common.brief([], {}, language="es")["language"] == "Spanish"
    assert "`language`" in llm_common.SYSTEM
    monkeypatch.setattr(builder, "provider", lambda: "anthropic")
    got = {}

    async def fake(clips, sources, **k):
        got.update(k)
        return P.build(clips, sources), {"source": "llm", "reason": "", "copy": {}, "notes": []}
    monkeypatch.setattr(llm_plan, "build", fake)
    _run(builder.build([{"id": "c0", "channel": "n"}], {"c0": ("/c0.mp4", 40.0)}, language="es"))
    assert got["language"] == "es"


# ── where the person can choose it ───────────────────────────────────────────

def test_the_picker_is_where_people_look_for_it():
    """Settings, and — where Luc looked — the editor's Captions panel, and the
    Auto Edit tab. All save the one account setting through /prefs."""
    from src.dashboard.aurora_html import DASHBOARD_HTML as h
    picker = h[h.index("function LanguagePicker("):h.index("function SettingsScreen(")]
    assert "fetch('/prefs', {method: 'PUT'" in picker and "JSON.stringify({language: code})" in picker
    settings = h[h.index("function SettingsScreen("):]
    assert "putPref({language: e.target.value})" in settings[:settings.index("Notifications</h3>")]
    editor = h[h.index("function ClipEditor("):]
    captions = editor[editor.index('<div className="ed-sec-t">Captions</div>'):][:2400]
    assert "<LanguagePicker me={me}/>" in captions
    assert "capLang !== accountLang" in captions, "old-language captions are not called out"
    assert "autoEditOn={!!(me && me.is_admin)} me={me}/>" in h, "the editor is never given the account"
    auto = h[h.index("function AutopilotScreen("):]
    assert '<LanguagePicker me={me} label="Captions and titles in"/>' in auto
    assert "msg.event==='prefs_changed'" in h, "the picker relies on prefs_changed reaching every tab"
