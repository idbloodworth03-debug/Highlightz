"""The VOD scan with the audio pass switched ON.

THE BUG. run_vod_analysis referenced a bare `vod_url` that was never a
parameter and never assigned, so the moment VOD_AUDIO_ENABLED was set every
scan died with `name 'vod_url' is not defined` before finding a single moment.

WHY NOTHING CAUGHT IT. tests/test_vod_audio.py covers extract_db_timeline and
score_timeline directly, and asserts the flag defaults to False — but no test
ever ran the ANALYZER with the flag on, so the branch holding the bug was never
executed. A unit test of the audio module could not find this; only running the
pipeline the way production runs it could.

That is the shape of the gap these tests close: the flag-on path is now
exercised end to end, so an undefined name in it fails here instead of on a
user's first scan.
"""

import asyncio

import pytest


@pytest.fixture
def scan(monkeypatch, tmp_path):
    """A VOD scan with everything external stubbed at its real boundary."""
    from config.settings import settings
    from src.vod import analyzer, vod_audio
    from src.profiles import manager as profile_manager

    monkeypatch.setattr(settings, "vod_audio_enabled", True)

    seen = {"audio_url": None}

    async def _extract(url, on_progress=None, timeout_s=0):
        seen["audio_url"] = url
        return {t: -20.0 for t in range(100, 140)}
    monkeypatch.setattr(vod_audio, "extract_db_timeline", _extract)

    async def _token(): return "apptoken"
    async def _info(vod_id, token):
        # Mirrors the real fetch_vod_info return shape exactly — see
        # test_the_stub_matches_the_real_fetch_vod_info_contract below, which
        # fails if this drifts from the function it stands in for.
        return {"id": vod_id, "title": "big stream", "channel": "lacy",
                "game": "VALORANT", "duration": 3600.0, "thumbnail_url": "",
                "url": f"https://www.twitch.tv/videos/{vod_id}"}
    async def _chat(vod_id, duration=0, on_progress=None):
        # The real shape fetch_vod_chat returns: offset / text / author.
        return [{"offset": 100 + (i % 20), "text": "POGGERS INSANE CLIP IT",
                 "author": f"viewer{i}"} for i in range(400)]

    monkeypatch.setattr(analyzer, "_get_app_token", _token)
    monkeypatch.setattr(analyzer, "fetch_vod_info", _info)
    monkeypatch.setattr(analyzer, "fetch_vod_chat", _chat)

    out = {"errors": [], "moments": [], "done": [], "seen": seen}

    async def on_progress(pct, data=None): pass
    async def on_moment(m): out["moments"].append(m)
    async def on_done(*a, **k): out["done"].append(a)
    async def on_error(msg): out["errors"].append(msg)

    def run(vod_id="123456", channel="", preset="default"):
        asyncio.run(analyzer.run_vod_analysis(
            vod_id, channel, preset, "u1",
            on_progress, on_moment, on_done, on_error))
        return out

    return run


def _returned_keys(fn) -> set:
    """String keys of the dict a function RETURNS.

    Scoped to `return` statements on purpose: walking every dict literal in the
    body also picks up the request headers, which are not part of the contract
    and made this compare Authorization and Client-ID against the payload.
    """
    import ast
    keys = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys |= {k.value for k in node.value.keys
                     if isinstance(k, ast.Constant) and isinstance(k.value, str)}
    return keys


def test_a_scan_with_audio_enabled_does_not_crash(scan):
    """THE regression. Before the fix this produced exactly one thing: the
    string "name 'vod_url' is not defined", shown to the user as the scan
    result."""
    out = scan()
    assert out["errors"] == [], f"the scan failed: {out['errors']}"
    assert out["done"], "the scan never completed"


def test_the_audio_pass_is_given_the_real_vod_url(scan):
    """streamlink is handed this string directly, so a bare id or a malformed
    URL means the decode fails and every scan silently loses its audio signal —
    a quieter version of the same bug."""
    out = scan(vod_id="987654")
    assert out["seen"]["audio_url"] == "https://www.twitch.tv/videos/987654"


def test_the_scan_still_finds_moments_with_audio_on(scan):
    """Not crashing is not the same as working. The audio pass must not swallow
    the chat signal on its way through."""
    out = scan()
    assert out["moments"], "a hot VOD produced no moments at all"


def test_the_scan_still_works_with_audio_off(monkeypatch, scan):
    """The partner test: the fix must not have moved the failure to the other
    branch, which is the one production runs by default."""
    from config.settings import settings
    monkeypatch.setattr(settings, "vod_audio_enabled", False)
    out = scan()
    assert out["errors"] == []
    assert out["done"]
    assert out["seen"]["audio_url"] is None, "audio ran while disabled"


def test_the_analyzer_defines_every_name_it_uses():
    """A cheap guard against the exact class of bug: a name used in
    run_vod_analysis that is neither a parameter, nor assigned in it, nor
    available at module level. Nested scopes are walked so the comprehension
    and inner-function locals that produced false positives are counted as
    defined."""
    import ast
    import builtins
    import pathlib

    src = pathlib.Path("src/vod/analyzer.py").read_text()
    tree = ast.parse(src)

    module_names = set(dir(builtins))
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module_names |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            module_names.add(node.name)
        elif isinstance(node, ast.Assign):
            module_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}

    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name == "run_vod_analysis")

    defined = set(module_names)
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            defined.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
            defined |= {a.arg for a in node.args.args}
        elif isinstance(node, ast.Lambda):
            # `key=lambda kv: -kv[1]` — a lambda's parameter is a binding too,
            # and missing it makes this test cry wolf on ordinary sort keys.
            defined |= {a.arg for a in node.args.args}
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            defined |= {a.asname or a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, (ast.comprehension,)):
            defined |= {n.id for n in ast.walk(node.target) if isinstance(n, ast.Name)}
    defined |= {a.arg for a in fn.args.args}

    used = {n.id for n in ast.walk(fn)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    undefined = sorted(used - defined)
    assert undefined == [], f"run_vod_analysis uses undefined names: {undefined}"


def test_the_analyzer_only_reads_keys_fetch_vod_info_actually_returns():
    """The gap a stubbed test cannot see by itself.

    Every test in this file feeds the analyzer a FAKE info dict. If that dict
    and the real fetch_vod_info disagree, the tests pass and production raises
    KeyError — which is the same failure mode as the vod_url bug, one layer
    out. So the two are compared directly: every key the analyzer reads off
    `info` must be a key fetch_vod_info puts in it.
    """
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/vod/analyzer.py").read_text())

    fetch = next(n for n in ast.walk(tree)
                 if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                 and n.name == "fetch_vod_info")
    returned = _returned_keys(fetch)
    assert returned, "could not read fetch_vod_info's return shape"

    analysis = next(n for n in ast.walk(tree)
                    if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                    and n.name == "run_vod_analysis")
    read = set()
    for node in ast.walk(analysis):
        # info["x"] and info.get("x")
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "info"
                and isinstance(node.slice, ast.Constant)):
            read.add(node.slice.value)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "info" and node.args
                and isinstance(node.args[0], ast.Constant)):
            read.add(node.args[0].value)

    assert read, "the analyzer stopped reading info at all — has it been renamed?"
    missing = sorted(read - returned)
    assert missing == [], \
        f"run_vod_analysis reads {missing} off info, which fetch_vod_info never returns"


def test_the_stub_in_this_file_matches_that_contract():
    """And the stub must match too, or these tests are exercising a shape
    production never produces."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/vod/analyzer.py").read_text())
    fetch = next(n for n in ast.walk(tree)
                 if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
                 and n.name == "fetch_vod_info")
    real = _returned_keys(fetch)

    stub = ast.parse(pathlib.Path(__file__).read_text())
    fn = next(n for n in ast.walk(stub)
              if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
              and n.name == "_info")
    stub_keys = _returned_keys(fn)

    assert stub_keys == real, (
        f"the stub has drifted from fetch_vod_info — "
        f"missing {sorted(real - stub_keys)}, extra {sorted(stub_keys - real)}")
