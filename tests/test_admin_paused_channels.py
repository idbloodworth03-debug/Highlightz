"""The admin page says which channels are paused, not just how many there are.

Owner, 2026-11-19: "it still doesn't look like the reaper is removing the
channels". The reaper PAUSES a Free account's channels, never deletes them:
a paused channel stays on its owner's list so it comes back with them, runs
nothing and holds no slot (docs/HANDOFF.md, the Free sections; deleting is
what lost people's channel lists before). But the admin page counted every
listed channel the same way: the users table's Streams column was the total,
and the account drawer listed paused channels under "Monitored channels". A
paused account read exactly like one being watched. Held here: the table and
the drawer say watched and paused apart, and why each paused one is paused.
"""

import asyncio
import re
import subprocess

import pytest

from src.dashboard import api


def _admin_js() -> str:
    return "\n".join(re.findall(r"<script>(.*?)</script>", api.ADMIN_HTML, re.S))


def test_the_users_list_counts_the_paused_apart(monkeypatch):
    from src.auth import users as user_store
    from src.stats import stream_stats

    async def no_outcomes():
        return {}
    monkeypatch.setattr(stream_stats, "totals_by_user_shared", no_outcomes)
    monkeypatch.setattr(api, "_require_admin", lambda request: None)
    monkeypatch.setattr(user_store, "get_all", lambda: [
        {"id": "u1", "username": "nova"}, {"id": "u2", "username": "kai"}, {"id": "u3", "username": "zed"}])
    monkeypatch.setattr(api, "_streams", {
        "u1:a": {"user_id": "u1", "status": "live"},
        "u1:b": {"user_id": "u1", "status": "paused", "hold": "queue_full"},
        "u2:a": {"user_id": "u2", "status": "paused"},
        "u3:a": {"user_id": "u3", "status": "offline"}})
    monkeypatch.setattr(api, "_clips", {})
    rows = {u["id"]: u for u in asyncio.run(api.admin_list_users(None))}
    assert [(rows[u]["stream_count"], rows[u]["streams_paused"]) for u in ("u1", "u2", "u3")] == [(2, 1), (1, 1), (1, 0)]


def _run(fn_names, expr):
    js = _admin_js()
    code = "function n0(n){ return String(n); }\n"
    for name in fn_names:
        start = js.index(f"function {name}(")
        code += js[start:js.index("\n}\n", start) + 3]
    got = subprocess.run(["node", "-e", code + f"\nconsole.log(JSON.stringify({expr}));"],
                         capture_output=True, text=True, timeout=30)
    assert got.returncode == 0, got.stderr
    import json
    return json.loads(got.stdout)


def test_the_streams_cell_shows_what_is_watched_and_beneath_it_what_is_paused():
    cells = _run(["streamsCell"], "[streamsCell({stream_count: 3, streams_paused: 1}),"
                                  " streamsCell({stream_count: 1, streams_paused: 1}),"
                                  " streamsCell({stream_count: 2}), streamsCell({})]")
    assert cells[0] == ('<td class="num" title="2 watched, 1 paused (they stay on the list and run nothing)">'
                        '2<div class="sub">1 paused</div></td>')
    assert cells[1].startswith('<td class="num" title="0 watched, 1 paused') and ">0<div" in cells[1]
    assert cells[2] == '<td class="num" title="">2</td>' and cells[3] == '<td class="num" title="">0</td>'
    assert "+ streamsCell(u)" in _admin_js()
    assert "'<td class=\"num\">' + (u.stream_count||0) + '</td>'" not in _admin_js()


@pytest.mark.parametrize("rec, said", [
    ({"status": "paused", "hold": "queue_full"}, "paused · review queue full"),
    ({"status": "paused", "hold": "billing"}, "paused · card failing"),
    ({"status": "paused"}, "paused · away 8 hours"),
])
def test_each_paused_channel_says_why(rec, said):
    import json
    assert _run(["pausedWhy"], f"pausedWhy({json.dumps(rec)})") == said


def test_the_drawer_lists_watched_first_then_paused_and_says_what_paused_means():
    js = _admin_js()
    start = js.index("// Watched first, then paused, and each paused one says why")
    drawer = js[start:js.index("// LIFETIME decisions", start)]
    assert "'<span style=\"flex:1\">Channels (' + streams.length + ')'" in drawer
    assert "' watched, ' + n0(pausedN) + ' paused'" in drawer
    assert "Monitored streams (" not in js, "paused channels are listed as monitored again"
    assert "'<span class=\"dim mono\">' + esc(pausedWhy(s)) + '</span>'" in drawer
    assert "html += ordered.length" in drawer
    assert "A paused channel runs nothing and holds no slot. ' " in drawer or "A paused channel runs nothing and holds no slot." in drawer
    assert "(a full review queue: once they accept or reject a clip)" in drawer
    # The admin can still stop any of them.
    assert "dr-stopone" in drawer and "dr-stopall" in drawer


def test_the_feedback_pages_drawer_says_it_too_with_its_own_words():
    page = api._ADMIN_FEEDBACK_HTML
    assert "Monitored channels (" not in page
    assert "const pausedSays = s => s.hold === 'queue_full' ? 'paused · review queue full'" in page
    # That page has none of the main page's helpers.
    assert "pausedWhy(" not in page and "dotClass(" not in page and "streamsCell(" not in page


@pytest.mark.parametrize("name", ["ADMIN_HTML", "_ADMIN_FEEDBACK_HTML", "_ADMIN_OPTOUT_HTML"])
def test_every_admin_pages_script_parses(name, tmp_path):
    """A syntax error blanks the whole admin page. The first draft of this
    change declared a name the feedback page already had, and it would have."""
    js = "\n".join(re.findall(r"<script>(.*?)</script>", getattr(api, name), re.S))
    f = tmp_path / "page.js"
    f.write_text(js)
    got = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=30)
    assert got.returncode == 0, got.stderr[:400]


def test_the_overview_tile_already_left_paused_channels_out():
    import inspect
    src = inspect.getsource(api.admin_overview)
    assert '"monitored": len(_counted_stream_keys()),' in src
    assert '"paused": sum(1 for s in _streams.values() if s.get("status") == "paused"),' in src
    assert "if(s.paused) st.push(n0(s.paused) + ' paused');" in _admin_js()
