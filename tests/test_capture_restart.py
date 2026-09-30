"""Capture pipelines back off when a stream keeps refusing, and never log a
playback token (2026-09-30 audit of the production journal: a Kick channel's
recorder and audio meter restarted every ~20s forever, each attempt logging the
signed playback URL).

What these defend:
  1. a pipeline that had been running comes back after 15s every time (a live
     stream's blip recovers exactly as before);
  2. one that dies again straight away waits longer each time, capped at 5 min;
  3. a token in a logged URL is replaced, the host and path kept;
  4. both pipelines use the backoff and the redaction.
"""

import inspect

from src.ingestion import audio_meter, clip_recorder
from src.ingestion.restart import Backoff, redact


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def test_a_stream_that_was_running_restarts_after_15s_every_time():
    c = Clock(); b = Backoff(clock=c)
    for _ in range(5):
        b.started(); c.t += 600          # ran ten minutes, then dropped
        assert b.next_delay() == 15.0


def test_a_stream_that_keeps_refusing_backs_off_to_five_minutes():
    c = Clock(); b = Backoff(clock=c)
    delays = []
    for _ in range(8):
        b.started(); c.t += 5            # died five seconds after launch
        delays.append(b.next_delay())
    assert delays == [15, 30, 60, 120, 240, 300, 300, 300]


def test_one_healthy_run_resets_the_backoff():
    c = Clock(); b = Backoff(clock=c)
    for _ in range(4):
        b.started(); c.t += 5; b.next_delay()
    b.started(); c.t += 120
    assert b.next_delay() == 15.0


def test_the_playback_token_never_reaches_the_log():
    line = ("error: Unable to open URL: https://fa7.us-west-2.playback.live-video.net/api/video/v1/"
            "x.channel.FDeS.m3u8?token=eyJhbGciOiJFUzM4NCJ9.eyJleHAiOjE3OTB9.S5L2C77k&player=web "
            "(403 Client Error: Forbidden for url: https://h/x.m3u8?sig=abc123&token=eyJxyz)")
    out = redact(line)
    assert "eyJ" not in out and "abc123" not in out
    assert "playback.live-video.net/api/video/v1/x.channel.FDeS.m3u8?token=<redacted>" in out
    assert "&player=web" in out and "403 Client Error" in out


def test_both_pipelines_back_off_and_redact():
    for mod in (audio_meter, clip_recorder):
        src = inspect.getsource(mod)
        assert "self._backoff.next_delay()" in src and "self._backoff.started()" in src
        assert "line=redact(line)" in src
        assert "asyncio.sleep(_RESTART_DELAY)" not in src
