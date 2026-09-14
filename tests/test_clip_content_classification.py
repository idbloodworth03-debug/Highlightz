"""Twitch refusing to classify a broadcast, and what the user is told about it.

THE SYMPTOM WAS "the clips won't load". The cause was not the player: Twitch's
Create Clip returns "Failed to determine content classification" and refuses,
so no clip is ever made. It shows up on channels streaming content Twitch
treats as intended for certain audiences — the labels are unset, or still
resolving.

THE OLD BEHAVIOUR WAS WORSE THAN THE FAILURE. create_clip retried three times,
gave up, returned None, and the generic handler told the user "it'll try again
on the next moment". On a channel in this state that sentence is untrue every
time it is said, and it was said on every trigger — the same pathology that
ClipTitleRejectedError was carved out to fix, on a channel where the score was
spiking correctly and nothing ever arrived.

SO IT BEHAVES LIKE THE AUTOMOD CASE, not like the not-authorized one. It is NOT
permanent: it clears when Twitch resolves the rating or the streamer sets their
labels. The channel keeps being monitored, backs off, and says once what is
actually wrong and who can fix it.
"""

import asyncio
import re

import pytest

from src.output import twitch_clips

REAL_BODY = ('{"error":"Bad Request","status":400,'
             '"message":"Failed to determine content classification"}')


# ── recognising it ───────────────────────────────────────────────────────────

def test_the_real_error_body_is_recognised():
    assert twitch_clips.is_classification_error(400, REAL_BODY)


def test_it_is_matched_on_the_message_not_the_status():
    """It has been seen on more than one status, and the status alone covers
    unrelated failures that belong on the generic retry path."""
    for status in (400, 403, 500):
        assert twitch_clips.is_classification_error(
            status, "Failed to determine content classification")


def test_it_does_not_collide_with_the_other_two_refusals():
    """Three different causes, three different correct responses. Confusing any
    pair means stopping a channel that would have recovered, or retrying one
    that never will."""
    automod = '{"message":"Title did not pass automod."}'
    notauth = '{"message":"User not authorized to create clips"}'
    assert not twitch_clips.is_classification_error(400, automod)
    assert not twitch_clips.is_classification_error(403, notauth)
    assert not twitch_clips.is_title_rejected(400, REAL_BODY)
    assert not twitch_clips.is_not_authorized(403, REAL_BODY)


def test_an_empty_body_is_not_it():
    assert not twitch_clips.is_classification_error(400, "")
    assert not twitch_clips.is_classification_error(400, None)


def test_the_error_is_not_a_subclass_of_the_permanent_one():
    """ClipNotAuthorizedError STOPS the channel. If this inherited from it, an
    `except ClipNotAuthorizedError` would catch this too and stop monitoring a
    channel that was going to start working again on its own."""
    assert not issubclass(twitch_clips.ClipClassificationError,
                          twitch_clips.ClipNotAuthorizedError)
    assert issubclass(twitch_clips.ClipClassificationError, RuntimeError)


# ── create_clip ──────────────────────────────────────────────────────────────

@pytest.fixture
def helix(monkeypatch):
    """Drive create_clip against a scripted Helix response."""
    import aiohttp

    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body
            self.headers = {}

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def text(self): return self._body

        async def json(self):
            import json
            return json.loads(self._body)

    class _Session:
        def __init__(self, status, body):
            self._status, self._body = status, body
            self.calls = 0

        def post(self, *a, **k):
            self.calls += 1
            return _Resp(self._status, self._body)

        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    def _install(status, body):
        sess = _Session(status, body)
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: sess)
        return sess
    return _install


def test_it_raises_rather_than_returning_none(helix):
    """None lands on the generic handler, which promises another attempt. The
    whole point of this change is that the promise is false here."""
    helix(400, REAL_BODY)
    with pytest.raises(twitch_clips.ClipClassificationError):
        asyncio.run(twitch_clips.create_clip("tok", "123", retry_delay=0))


def test_it_does_use_its_retries_first(helix):
    """UNLIKE automod, which cannot succeed on a second attempt. This one often
    does: Twitch is frequently just slow to classify the first clip of a
    session, so the retries are worth spending before giving up."""
    sess = helix(400, REAL_BODY)
    with pytest.raises(twitch_clips.ClipClassificationError):
        asyncio.run(twitch_clips.create_clip("tok", "123", retries=3, retry_delay=0))
    assert sess.calls == 3, f"gave up after {sess.calls} attempts instead of 3"


def test_a_late_success_still_produces_a_clip(helix, monkeypatch):
    """The retry has to actually work, not just cost time."""
    import aiohttp

    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body
            self.headers = {}
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def text(self): return self._body
        async def json(self):
            import json
            return json.loads(self._body)

    class _Flaky:
        def __init__(self): self.calls = 0
        def post(self, *a, **k):
            self.calls += 1
            if self.calls < 3:
                return _Resp(400, REAL_BODY)
            return _Resp(202, '{"data":[{"id":"LateSlug"}]}')
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

    sess = _Flaky()
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: sess)
    assert asyncio.run(
        twitch_clips.create_clip("tok", "123", retries=3, retry_delay=0)) == "LateSlug"


def test_unrelated_failures_still_return_none(helix):
    """The generic path is untouched — those SHOULD be retried next moment."""
    helix(400, '{"message":"Missing required parameter"}')
    assert asyncio.run(twitch_clips.create_clip("tok", "123", retry_delay=0)) is None


def test_a_successful_create_is_unaffected(helix):
    helix(202, '{"data":[{"id":"SlugHere"}]}')
    assert asyncio.run(twitch_clips.create_clip("tok", "123")) == "SlugHere"


# ── what the user is told, and how often ─────────────────────────────────────

def _handler_source() -> str:
    """The ClipClassificationError branch, with adjacent string literals spliced.

    The message is written across several f-string lines, so "Content
    Classification Labels" is not contiguous in the raw source — asserting
    against the source directly tests how the code is wrapped, not what the
    user reads.
    """
    import inspect
    import src.main as m
    src = inspect.getsource(m)
    handler = src[src.index("except twitch_clips.ClipClassificationError:"):]
    handler = handler[:handler.index("except twitch_clips.ClipNotAuthorizedError:")]
    # Splice `"..." f"..."` pairs the way Python does, then collapse the
    # indentation that survived between them.
    spliced = re.sub(r'"\s*f?"', "", handler)
    return re.sub(r"\s+", " ", spliced)


def test_the_channel_is_backed_off_rather_than_stopped():
    """The difference from ClipNotAuthorizedError, which stops the stream.

    Stopping here would be wrong twice over: the channel recovers by itself,
    and on a plan capped at 1 or 3 streams the user would have to notice and
    re-add it.
    """
    handler = _handler_source()
    assert "stop_stream_internal" not in handler, \
        "a recoverable classification failure stops the channel"
    assert "_classification_until" in handler, "the channel is not backed off"


def test_the_user_is_told_once_per_window_not_once_per_trigger():
    handler = _handler_source()
    assert "_first" in handler, "the notice is not gated on the first failure"
    assert "if _uid and _first:" in handler


def test_the_message_says_who_can_actually_fix_it():
    """Neither we nor the user can. Saying so is the difference between a
    useful notice and a scary one."""
    handler = _handler_source()
    assert "Content Classification Labels" in handler
    assert "streamer" in handler, "it does not name who sets the labels"
    assert "Nothing is wrong with your account" in handler
    assert "resumes" in handler, "it does not say it recovers on its own"
    assert "try again on the next moment" not in handler, \
        "the untrue generic promise is back"


def test_a_backed_off_channel_is_skipped_before_the_expensive_part():
    """The saving is the post-roll sleep and the Helix call, both of which come
    after this guard. Skipping later would back off in name only."""
    import src.main as m
    src = __import__("inspect").getsource(m)
    guard = src.index("_classification_until.get(job.channel")
    assert guard < src.index("post_roll_capped") if "post_roll_capped" in src else True
    assert "clip_skipped_classification" in src


def test_the_backoff_window_is_shorter_than_the_automod_one():
    """They clear differently. An automod title stays wrong until the streamer
    renames the stream; classification often resolves a minute or two into a
    broadcast, so waiting the full automod window would sit out clips that
    would have worked."""
    import src.main as m
    assert m._CLASSIFICATION_BACKOFF_S < m._TITLE_AUTOMOD_BACKOFF_S
    assert m._CLASSIFICATION_BACKOFF_S >= 60, \
        "too short to stop burning a Helix call on every trigger"


# ── the tally that answers "is this everywhere or one channel" ───────────────

@pytest.fixture()
def refusals(tmp_path, monkeypatch):
    from src.stats import clip_refusals as cr
    monkeypatch.setattr(cr, "_FILE", tmp_path / "clip_refusals.json")
    return cr


def test_a_refusal_is_counted_per_channel(refusals):
    refusals.record("aceu", refusals.CLASSIFICATION)
    refusals.record("aceu", refusals.CLASSIFICATION)
    refusals.record("lacy", refusals.TITLE_AUTOMOD)
    rows = {r["channel"]: r for r in refusals.all_rows()}
    assert rows["aceu"]["count"] == 2
    assert rows["lacy"]["count"] == 1
    assert rows["aceu"]["reason"] == refusals.CLASSIFICATION


def test_a_channel_that_clips_again_drops_off_the_list(refusals):
    """Without this the tally only grows and a channel that recovered weeks ago
    still reads as broken — which makes the admin list useless within a month."""
    refusals.record("aceu", refusals.CLASSIFICATION)
    assert refusals.all_rows()
    refusals.clear("aceu")
    assert refusals.all_rows() == []


def test_the_success_path_actually_clears_it():
    """The clear() call has to be on the path a real clip takes, not just
    available. It is the only thing that keeps the list current."""
    import inspect
    import src.main as m
    src = inspect.getsource(m)
    i = src.index("meta = await asyncio.wait_for(processor.process(job)")
    # Bounded by the NEXT statement rather than a character count. The count was
    # 600 and a comment growing by four lines pushed the call outside it, which
    # failed as "a successful clip does not clear the refusal record" — a claim
    # about behaviour that was not true. A landmark cannot drift; a number can.
    window = src[i:src.index("await dashboard_api.notify_clip_ready", i)]
    assert "_refusals.clear(job.channel)" in window, \
        "a successful clip does not clear the channel's refusal record"


def test_the_newest_problem_is_listed_first(refusals):
    import time as _t
    refusals.record("old", refusals.CLASSIFICATION)
    _t.sleep(0.01)
    refusals.record("new", refusals.CLASSIFICATION)
    assert [r["channel"] for r in refusals.all_rows()][0] == "new"


def test_all_three_refusal_causes_are_recorded():
    """They have three different fixes, so collapsing them would lose the only
    thing that makes the list actionable."""
    import inspect
    import src.main as m
    src = inspect.getsource(m)
    for reason in ("CLASSIFICATION", "TITLE_AUTOMOD", "NOT_AUTHORIZED"):
        # The user id is part of the call now: the same warning is shown to the
        # user whose channel it is, and an unattributed refusal reaches nobody.
        assert f"_refusals.record(_ch, _refusals.{reason}, _uid)" in src, \
            f"{reason} refusals are not recorded against the affected user"


def test_a_corrupt_or_missing_store_is_survivable(refusals, tmp_path):
    """A diagnostic counter must never be the reason the clip pipeline breaks."""
    assert refusals.all_rows() == []          # missing file
    (tmp_path / "clip_refusals.json").write_text("{not json")
    assert refusals.all_rows() == []          # corrupt file
    refusals.record("aceu", refusals.CLASSIFICATION)   # and still writable
    assert refusals.all_rows()[0]["channel"] == "aceu"


def test_the_admin_panel_hides_itself_when_nothing_is_wrong():
    """An empty panel on a healthy box trains you to ignore the row it lives
    in, so it only appears when there is something to act on."""
    from src.dashboard.api import ADMIN_HTML
    assert 'id="refusals-box"' in ADMIN_HTML
    assert 'style="display:none' in ADMIN_HTML.split('id="refusals-box"')[1][:80]
    assert "box.style.display='none'" in ADMIN_HTML


def test_the_admin_panel_explains_the_fix_not_just_the_code():
    """"classification" tells an admin nothing. Who can fix it does."""
    from src.dashboard.api import ADMIN_HTML
    assert "Content Classification Labels" in ADMIN_HTML
    assert "renames the stream" in ADMIN_HTML
    assert "clipping restricted" in ADMIN_HTML
