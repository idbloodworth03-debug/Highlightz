"""The rejection report (src/maintenance/rejection_report.py), on a made-up
ledger: it must count what the files say, split the window where it was
asked to, and never fall over on a missing file or a torn line, because it
is run on production to decide whether the detector needs changing."""

import json

import pytest

from src.maintenance import rejection_report as rr

NOW = 1_800_000_000.0
DAY = 86400


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "_ROOT", tmp_path)
    monkeypatch.setattr(rr, "_names", lambda: {"u1": "luclips", "u2": "nova"})
    ledger, training = [], []

    def verdict(uid, ch, plat, label, days_ago, delay_s, audio, lead="CHAT_VELOCITY", score=60):
        ts = NOW - days_ago * DAY
        cid = f"{uid}-{len(ledger)}"
        ledger.append({"ts": ts - delay_s, "event": "caught", "user_id": uid, "channel": ch,
                       "clip_id": cid, "clip_at": ts - delay_s})
        ledger.append({"ts": ts, "event": label, "user_id": uid, "channel": ch,
                       "clip_id": cid, "clip_at": ts - delay_s})
        sigs = {"CHAT_VELOCITY": 0.2, "AUDIO_SPIKE": audio, "KEYWORD": 0.1}
        sigs[lead] = 0.9
        training.append({"ts": ts, "clip_id": cid, "channel": ch, "user_id": uid,
                         "platform": plat, "label": label, "trigger_score": score, "signals": sigs})

    # Before (8-20 days ago): u1 keeps most of their Kick clips.
    for i in range(8):
        verdict("u1", "westcol", "kick", "approved", 10 + i, 3600, audio=0.5, lead="AUDIO_SPIKE", score=70)
    for i in range(2):
        verdict("u1", "westcol", "kick", "rejected", 12 + i, 3600, audio=0.4)
    # Recent (last 7 days): u1 rejects most, fast, with no audio signal.
    for i in range(9):
        verdict("u1", "westcol", "kick", "rejected", 1 + i * 0.5, 30, audio=0.0, score=52)
    verdict("u1", "westcol", "kick", "approved", 2, 600, audio=0.3, lead="AUDIO_SPIKE", score=71)
    # u2 on Twitch is steady.
    for i in range(3):
        verdict("u2", "novafps", "twitch", "approved", 2 + i, 1800, audio=0.6, lead="AUDIO_SPIKE", score=75)
    verdict("u2", "novafps", "twitch", "rejected", 3, 7200 * 2, audio=0.2, score=58)
    # A Clear queue is not a rejection.
    ledger.append({"ts": NOW - 1 * DAY, "event": "cleared", "user_id": "u1", "channel": "westcol",
                   "clip_id": "x", "clip_at": NOW - 1 * DAY})

    def write():
        (tmp_path / "stream_stats.jsonl").write_text(
            "\n".join(json.dumps(r) for r in ledger) + "\n{torn line\n")
        (tmp_path / "training_log.jsonl").write_text(
            "\n".join(json.dumps(r) for r in training) + "\n")
    write()
    return tmp_path


def _run(**kw):
    lines = []
    rr.report(now=NOW, out=lines.append, **kw)
    return "\n".join(lines)


def test_the_windows_are_split_and_the_keep_rates_are_of_what_was_judged(files):
    text = _run(days=7, before=14)
    # Recent: u1 1 kept + 9 rejected, u2 3 kept + 1 rejected -> 4 of 14 = 29%.
    assert "last 7 days: keep 29% of 14 judged, 10 rejected" in text
    # Before: u1 8 kept + 2 rejected -> 80% of 10.
    assert "the 14 days before: keep 80% of 10 judged, 2 rejected" in text


def test_the_channel_with_the_most_rejections_comes_first_with_its_change(files):
    text = _run(days=7, before=14)
    who = text.split("== 2.")[1].split("== 3.")[0].splitlines()
    first = next(l for l in who if "westcol" in l or "novafps" in l)
    assert "luclips" in first and "westcol" in first
    assert " 10% " in first and " 80% " in first, first          # keep now vs before
    assert "0 min" in first or "1 min" in first, "a 30 s reject reads as a clear-out"
    second = next(l for l in who if "novafps" in l)
    assert "nova" in second and "4.0 h" in second


def test_what_is_rejected_by_platform_score_signal_and_missing_audio(files):
    text = _run(days=7, before=14)
    what = text.split("== 3.")[1]
    assert "kick    keep now  10% of   10 judged;  before  80% of   10" in what
    assert "twitch  keep now  75% of    4 judged" in what
    rejected = next(l for l in what.splitlines() if l.strip().startswith("rejected"))
    assert "n=  10" in rejected and "no audio  90%" in rejected and "led by: CHAT_VELOCITY 100%" in rejected
    approved = next(l for l in what.splitlines() if l.strip().startswith("approved"))
    assert "led by: AUDIO_SPIKE 100%" in approved and "no audio   0%" in approved


def test_days_run_in_date_order_across_a_new_year(files):
    """NOW is mid-January: the window spans December. Sorting "MM-DD" text put
    12-29 after 01-14 and marked it recent."""
    import re
    rows = re.findall(r"^ ([* ]) (\d\d-\d\d) ", _run(days=7, before=14), re.M)
    days = [d for _, d in rows]
    assert days[:3] == ["12-29", "12-30", "12-31"] and days[-1] == "01-14", days
    assert [d for m, d in rows if m == "*"] == ["01-10", "01-11", "01-12", "01-13", "01-14"], rows


def test_a_clear_queue_is_counted_apart_from_rejections(files):
    text = _run(days=7, before=14)
    day = [l for l in text.split("== 2.")[0].splitlines() if l.startswith(" *")]
    assert sum(int(l.split()[5]) for l in day) == 1          # the cleared column


def test_missing_files_print_an_empty_report_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(rr, "_ROOT", tmp_path)
    monkeypatch.setattr(rr, "_names", lambda: {})
    text = _run(days=7, before=14)
    assert "keep - of 0 judged" in text
