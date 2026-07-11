import time
import pytest
from src.chat.metrics import ChatMetrics


def test_velocity_increases_with_messages():
    m = ChatMetrics(window_seconds=10)
    for _ in range(50):
        m.ingest("hello world")
    assert m.current_velocity() > 0


def test_keyword_detection():
    m = ChatMetrics()
    m.ingest("clip it now!!")
    snap = m.snapshot()
    assert snap.keyword_hits >= 1
    assert snap.trigger_phrases >= 1


def test_no_false_positive_on_normal_chat():
    m = ChatMetrics()
    for msg in ["nice", "hey", "what's up", "lol", "cool stream"]:
        m.ingest(msg)
    snap = m.snapshot()
    assert snap.keyword_hits == 0


def test_velocity_spike_ratio_baseline():
    m = ChatMetrics()
    assert m.velocity_spike_ratio() == 1.0   # no long-term data yet


def test_snapshot_message_count():
    m = ChatMetrics(window_seconds=60)
    for i in range(10):
        m.ingest(f"message {i}")
    snap = m.snapshot()
    assert len(snap.messages) == 10


def test_last_message_ts_survives_window_pruning():
    # The heartbeat needs "last message EVER", not "last message in the 15s
    # window" — a quiet chat empties the window but must not read as never
    # connected.
    import time
    from unittest.mock import patch
    m = ChatMetrics(window_seconds=15)
    assert m.snapshot().last_message_ts == 0.0          # nothing ever ingested

    t0 = time.time()
    with patch("src.chat.metrics.time") as fake_time:
        fake_time.time.return_value = t0
        m.ingest("hello", author="a")
        # 10 minutes later: window and long-window both fully pruned
        fake_time.time.return_value = t0 + 600
        snap = m.snapshot()
    assert snap.velocity == 0.0                         # window is empty…
    assert snap.last_message_ts == t0                   # …but the heartbeat remembers
