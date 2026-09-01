"""Clip Review is an inbox, not a gallery.

It used to carry All / Pending / Approved chips, so the default view mixed
clips waiting on a decision with clips that already had one — while the Clip
Library showed the approved set in full, ordered by when you kept it. Two
screens for one set of clips, overlapping in the middle.

Review now shows PENDING ONLY. That is a small change to a filter and a large
change to what else on the screen is still telling the truth, which is what
most of this file is about:

  * "Cull clips" sits on this screen and used to delete clips of EVERY status,
    including approved ones the user could no longer even see from here.
  * Culling is by score, and a crowd suggestion has no score — so any threshold
    above zero would have deleted every one of them.
  * An empty grid used to mean one thing and now means three.
"""

import base64
import json as _j
import re
import time
from pathlib import Path

import pytest
from itsdangerous import TimestampSigner

SRC = Path("src/dashboard/aurora_html.py").read_text()
CSS = SRC.split('<script type="text/babel">')[0]
JS = SRC.split('<script type="text/babel">')[1]


def _review() -> str:
    m = re.search(r"function ReviewScreen\(.*?\n\}\n\n", JS, re.S)
    assert m, "ReviewScreen not found"
    return m.group(0)


def _code(js: str) -> str:
    js = re.sub(r"\{/\*.*?\*/\}", "", js, flags=re.S)
    return re.sub(r"//[^\n]*", "", js)


# ── the screen ───────────────────────────────────────────────────────────────

def test_the_grid_is_built_from_pending_clips_only():
    body = _code(_review())
    assert "Object.values(clips).filter(c=>c.status==='pending')" in body, \
        "Clip Review is drawing from every clip again"


def test_there_is_no_status_filter_left_to_reintroduce_them():
    """A filter defaulting to 'all' over a pending-only list is a trapdoor: it
    reads as harmless and puts approved clips straight back on the screen."""
    body = _code(_review())
    assert "setFilter" not in body and "filter===" not in body
    assert "rd-filters" not in body, "the status chips are back"


def test_the_dead_filter_state_went_with_it():
    """Left behind, it is a piece of state nothing reads and the next person has
    to work out whether it matters."""
    assert "const [filter, setFilter]" not in JS, \
        "the parent still holds review's old filter state"


def test_the_streamer_filter_and_sort_survive():
    """Those narrow a set that still exists. Removing the status chips is not a
    reason to strip the controls next to them."""
    body = _review()
    assert "ClipControls" in body and "setChanFilter" in body
    assert "sortClips(" in body


def test_suggestions_still_lead_the_queue_by_default():
    """The grouping that lifts highlights to the top must survive, and it must
    be what a user gets without asking.

    The fourth argument was the boolean `true` and is a mode string now: the
    grouping became a CHOICE, because applying it before the sort key meant
    "date added" silently meant "highlights, then everything else by date" and
    there was no way to see one true sequence. Default unchanged; the option to
    turn it off is the new part. tests/test_clip_sorting.py executes both
    orderings against fixture clips."""
    assert re.search(r"sortClips\(filtered,\s*sortBy,\s*sortDir,\s*group\)", JS), \
        "Clip Review stopped sorting as a queue, so highlights no longer lead"
    assert re.search(r"useState\('highlights'\)", JS), \
        "the queue no longer DEFAULTS to putting highlights first"


def test_the_empty_grid_tells_the_three_cases_apart():
    """Before this change an empty grid could only mean "you have never had a
    clip". It can now also mean "you have reviewed everything" — and telling
    somebody with 200 clips in their library to go add a channel reads as the
    app having lost their work. The streamer filter adds a third case."""
    body = _review()
    assert "You are all caught up" in body, \
        "a reviewed-everything queue still says there are no clips"
    assert "approvedElsewhere" in body, \
        "the empty state cannot tell caught-up from never-had-a-clip"
    assert "Nothing from" in body, \
        "a filtered-to-empty streamer reads as an empty queue"
    assert "Waiting for clips" in body, "the first-run empty state was lost"


def test_the_toolbar_count_is_measured_against_the_queue():
    """clipsArr is the pending set now, so "3 of 12" counts what this screen is
    actually showing rather than the whole store."""
    body = _code(_review())
    assert "shown.length === clipsArr.length" in body
    assert "clipsArr.length" in body


# ── culling, which lives on this screen ──────────────────────────────────────

@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from src.dashboard import api
    from src.auth import users as user_store
    from src.stats import stream_stats

    monkeypatch.setattr(user_store, "_USERS_FILE", tmp_path / "users.json")
    monkeypatch.setattr(user_store, "_BACKUP_FILE", tmp_path / "users.json.bak")
    monkeypatch.setattr(stream_stats, "_LOG_FILE", tmp_path / "stats.jsonl")
    monkeypatch.setattr(api, "_clips", {})
    monkeypatch.setattr(api, "_save_clips", lambda: None)
    monkeypatch.setattr(api, "_delete_clip_file", lambda c: None)

    async def _noop(*a, **k): return None
    monkeypatch.setattr(api, "broadcast", _noop)

    now = time.time()
    (tmp_path / "users.json").write_text(_j.dumps([
        {"id": "u1", "username": "one", "subscription_status": "active",
         "created_at": now}]))

    c = TestClient(api.app)
    signer = TimestampSigner(api.settings.dashboard_secret_key)
    c.cookies.set("session", signer.sign(base64.b64encode(_j.dumps(
        {"auth": True, "user_id": "u1", "username": "one", "is_admin": False,
         "subscription_status": "active"}).encode())).decode())

    def add(cid, status="pending", score=80.0, **extra):
        api._clips[cid] = {"id": cid, "user_id": "u1", "status": status,
                           "channel": "novaplays", "created_at": now - 60,
                           "trigger_score": score, "trigger_signals": [], **extra}
    c.add = add
    c.api = api
    yield c
    api._clips.clear()


def test_culling_never_touches_the_library(client):
    """THE ONE THIS CHANGE MADE URGENT. "Cull clips" sits on Clip Review, which
    now shows pending clips and nothing else — so a cull that reached past them
    would delete work the user had already decided to keep, from a screen that
    cannot show it to them. Same rule clear-queue has always had."""
    client.add("keep_me", status="approved", score=10.0)
    client.add("cull_me", status="pending", score=10.0)
    r = client.post("/clips/bulk-cull", json={"min_score": 50})
    assert r.status_code == 200, r.text
    assert r.json()["removed"] == 1
    assert "keep_me" in client.api._clips, "culling deleted an approved clip"
    assert "cull_me" not in client.api._clips


def test_culling_never_deletes_a_crowd_suggestion(client):
    """A suggestion carries score 0 because NOTHING scored it — that is the
    entire point of the feature. So every threshold above zero matched all of
    them, and a user culling at 50 to tidy up weak clips would have silently
    wiped every moment the crowd found. Score-based culling has no opinion to
    offer about a clip that was never scored."""
    client.add("sug", status="pending", score=0.0, suggested=True)
    client.add("weak", status="pending", score=10.0)
    r = client.post("/clips/bulk-cull", json={"min_score": 50})
    assert r.status_code == 200, r.text
    assert "sug" in client.api._clips, "culling deleted a crowd suggestion"
    assert "weak" not in client.api._clips, "culling stopped working entirely"
    assert r.json()["removed"] == 1


def test_culling_still_removes_what_it_is_for(client):
    """The guards have to be narrow. Two carve-outs and a status check is
    plenty of room for the feature to stop doing anything at all."""
    for i in range(4):
        client.add(f"low{i}", status="pending", score=10.0)
    client.add("high", status="pending", score=90.0)
    r = client.post("/clips/bulk-cull", json={"min_score": 50})
    assert r.json()["removed"] == 4
    assert list(client.api._clips) == ["high"]


def test_the_cull_preview_counts_the_same_set_the_endpoint_acts_on():
    """The panel shows "remove N" before you press. Counting a different set
    than the endpoint deletes makes that number a promise the server breaks."""
    m = re.search(r"function CullPanel\(.*?\n\}\n\n", JS, re.S)
    assert m, "CullPanel not found"
    body = _code(m.group(0))
    assert "c.status === 'pending'" in body, \
        "the cull preview still counts approved clips the endpoint will not touch"
    assert "!c.suggested" in body, \
        "the cull preview promises to delete suggestions the endpoint keeps"
