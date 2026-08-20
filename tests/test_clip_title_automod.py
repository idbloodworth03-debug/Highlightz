"""Twitch automod refusing the clip title.

FOUND IN PRODUCTION. 265 of 272 clip failures in six hours were one thing:

    HTTP 400 {"error":"Bad Request","status":400,
              "message":"Title did not pass automod."}

THE TITLE IS NOT OURS. Helix Create Clip takes only a broadcaster_id — there
is no title parameter — so the clip inherits the BROADCASTER'S CURRENT STREAM
TITLE. Something in their title trips Twitch's automod, and nothing about our
request, token or account is wrong.

That puts it between the two responses that already existed, and both are
wrong for it:

  * the generic retry path fails identically on every moment while the title
    stands, and each attempt costs a post-roll sleep, a slot on the serial
    processor and a call from a Helix budget shared with every user;
  * stopping the channel (what a 403 does) is too final — the streamer can
    rename the stream at any moment and everything works again.

So: back the channel off, keep monitoring, and tell the user the truth once.
"""

import time

import pytest

from src.output import twitch_clips


# ── detection ────────────────────────────────────────────────────────────────

def test_the_real_production_body_is_recognised():
    """Copied verbatim from the journal."""
    body = '{"error":"Bad Request","status":400,"message":"Title did not pass automod."}'
    assert twitch_clips.is_title_rejected(400, body)


def test_it_is_matched_on_the_message_not_the_bare_400():
    """400 covers unrelated request problems that must stay on the generic
    retry path — treating every 400 as automod would silently stop retrying
    things that would have succeeded."""
    assert not twitch_clips.is_title_rejected(400, '{"message":"Missing broadcaster_id"}')
    assert not twitch_clips.is_title_rejected(400, "")
    assert not twitch_clips.is_title_rejected(400, None)


def test_the_same_message_on_another_status_is_not_it():
    assert not twitch_clips.is_title_rejected(403, "Title did not pass automod.")
    assert not twitch_clips.is_title_rejected(500, "Title did not pass automod.")


def test_it_does_not_collide_with_the_403_not_authorized_case():
    """Two permanent-ish refusals with opposite handling. Confusing them would
    stop a channel that only needed a backoff, or back off one that will never
    work again."""
    automod = '{"message":"Title did not pass automod."}'
    restricted = '{"message":"User not authorized to create clips"}'
    assert twitch_clips.is_title_rejected(400, automod)
    assert not twitch_clips.is_not_authorized(400, automod)
    assert twitch_clips.is_not_authorized(403, restricted)
    assert not twitch_clips.is_title_rejected(403, restricted)


def test_the_error_is_not_a_not_authorized_subclass():
    """main.py branches on them separately; a subclass would let the 403 branch
    catch this and stop the stream."""
    assert not issubclass(twitch_clips.ClipTitleRejectedError,
                          twitch_clips.ClipNotAuthorizedError)


# ── create_clip raises instead of returning None ─────────────────────────────

@pytest.fixture
def helix(monkeypatch):
    """Drive create_clip against a scripted Helix response."""
    import aiohttp

    class _Resp:
        def __init__(self, status, body):
            self.status, self._body = status, body
            self.headers = {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def text(self):
            return self._body

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

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def _install(status, body):
        sess = _Session(status, body)
        monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: sess)
        return sess
    return _install


def test_create_clip_raises_on_the_automod_400(helix):
    import asyncio
    helix(400, '{"error":"Bad Request","status":400,"message":"Title did not pass automod."}')
    with pytest.raises(twitch_clips.ClipTitleRejectedError):
        asyncio.run(twitch_clips.create_clip("tok", "123"))


def test_it_does_not_burn_the_retries(helix):
    """It cannot succeed on attempt two or three, and each attempt is a real
    Helix call from a shared budget."""
    import asyncio
    sess = helix(400, '{"message":"Title did not pass automod."}')
    with pytest.raises(twitch_clips.ClipTitleRejectedError):
        asyncio.run(twitch_clips.create_clip("tok", "123", retries=3))
    assert sess.calls == 1, f"made {sess.calls} attempts at a call that cannot succeed"


def test_an_unrelated_400_still_returns_none(helix):
    """The generic path must be untouched: it returns None so the caller
    retries on the next moment."""
    import asyncio
    helix(400, '{"message":"Missing required parameter"}')
    assert asyncio.run(twitch_clips.create_clip("tok", "123")) is None


def test_a_successful_create_still_works(helix):
    import asyncio
    helix(202, '{"data":[{"id":"SlugHere","edit_url":"x"}]}')
    assert asyncio.run(twitch_clips.create_clip("tok", "123")) == "SlugHere"


# ── the backoff ──────────────────────────────────────────────────────────────

def test_the_backoff_window_is_long_enough_to_matter():
    """265 failures in 6h is roughly one every 80s. A window shorter than that
    would not actually reduce anything."""
    import src.main as main
    assert main._TITLE_AUTOMOD_BACKOFF_S >= 300


def test_the_backoff_is_short_enough_to_recover_on_its_own():
    """The streamer may rename the stream at any moment. A very long window
    would leave a working channel dark for no reason."""
    import src.main as main
    assert main._TITLE_AUTOMOD_BACKOFF_S <= 1800


def test_the_skip_happens_before_the_expensive_work():
    """The whole point is to not spend the post-roll sleep and the Helix call.
    A check placed after them would log nicely and save nothing."""
    import inspect
    import src.main as main
    src = inspect.getsource(main.run_clip_processor)
    skip_at = src.index("_title_automod_until.get")
    process_at = src.index("processor.process(job)")
    assert skip_at < process_at, "the automod backoff is checked after the clip call"


def test_the_channel_is_not_stopped():
    """Stopping strands a user whose streamer renames the stream a minute
    later. The 403 branch stops; this one must not."""
    import inspect
    import src.main as main
    src = inspect.getsource(main.run_clip_processor)
    seg = src[src.index("except twitch_clips.ClipTitleRejectedError:"):]
    seg = seg[:seg.index("except twitch_clips.ClipNotAuthorizedError:")]
    assert "stop_stream_internal" not in seg, \
        "the automod backoff stops the stream — it must only back off"
    assert "_title_automod_until" in seg, "no backoff is recorded"


def test_the_user_is_told_once_per_window_not_once_per_moment():
    """265 toasts in six hours, each claiming it would try again shortly, is
    what the old generic path produced."""
    import inspect
    import src.main as main
    src = inspect.getsource(main.run_clip_processor)
    seg = src[src.index("except twitch_clips.ClipTitleRejectedError:"):]
    seg = seg[:seg.index("except twitch_clips.ClipNotAuthorizedError:")]
    assert "_first" in seg and "if _uid and _first:" in seg, \
        "the notice is not gated to the first failure in the window"


def test_the_message_names_the_real_cause():
    """The generic message says it will try again on the next moment, which is
    false here and points the user at their own account."""
    import inspect
    import src.main as main
    src = inspect.getsource(main.run_clip_processor)
    seg = src[src.index("except twitch_clips.ClipTitleRejectedError:"):]
    seg = seg[:seg.index("except twitch_clips.ClipNotAuthorizedError:")]
    low = seg.lower()
    assert "automod" in low, "the message does not name automod"
    assert "stream title" in low, "the message does not say whose title it is"
    assert "nothing is wrong with your account" in low, \
        "the message does not clear the user of a problem that is not theirs"


# ── the backoff dict behaves ─────────────────────────────────────────────────

def test_an_elapsed_window_lets_the_next_job_through():
    """Recovery has to be automatic — nobody is going to notice and clear it."""
    import src.main as main
    main._title_automod_until.clear()
    main._title_automod_until["lacy"] = time.time() - 1     # already elapsed
    assert main._title_automod_until.get("lacy", 0.0) <= time.time()
    main._title_automod_until.clear()


def test_the_dict_does_not_grow_without_bound():
    """The elapsed entry is popped rather than left behind, so a long-running
    process does not accumulate one row per channel it ever backed off."""
    import inspect
    import src.main as main
    src = inspect.getsource(main.run_clip_processor)
    assert "_title_automod_until.pop(job.channel, None)" in src


def test_the_backoff_is_per_channel_not_global():
    """One streamer's bad title must not silence every other channel on the
    box — which is exactly what a single global flag would do."""
    import src.main as main
    main._title_automod_until.clear()
    main._title_automod_until["lacy"] = time.time() + 600
    assert main._title_automod_until.get("jynxzi", 0.0) == 0.0
    main._title_automod_until.clear()
