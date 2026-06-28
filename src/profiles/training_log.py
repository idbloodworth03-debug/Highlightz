"""
Append-only outcome log for clip-quality feedback.

Records each clip's signal vector + final outcome (approved / rejected /
expired_unreviewed) so a future weight-learning pass (#4) has a real labeled
dataset — crucially including the NEGATIVE class, which explicit rejects alone
don't provide (users approve keepers and let the rest expire rather than
rejecting). Expired-but-unreviewed pending clips are the realistic source of
negatives.

This is WRITE-ONLY telemetry: nothing here feeds live scoring, so it can never
change what gets clipped. Writes are best-effort and never raise — logging must
not be able to break the clip pipeline.
"""

import json
import time
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)

# approved → positive; rejected → explicit negative; expired_unreviewed → weak
# negative (user had the chance to approve it and didn't before it aged out).
APPROVED          = "approved"
REJECTED          = "rejected"
EXPIRED_UNREVIEWED = "expired_unreviewed"

_LOG_FILE = Path(settings.local_storage_path) / "training_log.jsonl"


def log_outcome(clip: dict, label: str) -> None:
    """Append one labeled training example as a JSON line. Best-effort."""
    try:
        signals = {}
        for s in (clip.get("trigger_signals") or []):
            t = str(s.get("type", "")).replace("SignalType.", "")
            if t:
                signals[t] = round(float(s.get("value", 0.0) or 0.0), 4)
        # Skip records with no signal vector — they carry no training value
        # (e.g. VOD moments or legacy clips without trigger_signals).
        if not signals:
            return
        record = {
            "ts":             round(time.time(), 1),
            "clip_id":        clip.get("id"),
            "channel":        clip.get("channel"),
            "user_id":        clip.get("user_id"),
            "platform":       clip.get("platform"),
            "label":          label,
            "trigger_score":  clip.get("trigger_score") or clip.get("score"),
            "virality_score": clip.get("virality_score"),
            "signals":        signals,
        }
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        # A single append of one short line is atomic enough on POSIX for this.
        with _LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:  # never let telemetry break the clip pipeline
        log.warning("training_log_write_failed", error=str(exc))
