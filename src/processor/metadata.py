from dataclasses import dataclass, field
from typing import Any
import time
import uuid


@dataclass
class ClipMetadata:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    channel: str = ""
    platform: str = ""
    trigger_score: float = 0.0
    trigger_signals: list[dict] = field(default_factory=list)
    chat_snapshot: list[str] = field(default_factory=list)
    stream_title: str = ""
    game: str = ""
    created_at: float = field(default_factory=time.time)
    storage_url: str = ""
    duration_seconds: float = 0.0
    status: str = "pending"    # pending | approved | rejected | published
    virality_score: float = 0.0
    clip_title: str = ""
    vertical_url: str = ""
    user_id: str = ""
    # Twitch-hosted clip (Helix Clips API)
    twitch_clip_id: str = ""
    twitch_url: str = ""       # public watch page
    embed_url: str = ""        # iframe embed URL
    thumbnail_url: str = ""
    # Captured from a channel Twitch flags as intended for mature audiences.
    # NOT a judgement about the clip: it decides how the clip is PLAYED. An
    # age-gated clip cannot be shown in our embedded player, because Twitch
    # cannot confirm a viewer's age inside a third-party iframe, so the
    # dashboard sends these straight to Twitch rather than rendering a frame
    # that will sit there black. Read off the Get Streams response the worker
    # already makes for liveness, so it costs no extra Helix call.
    age_restricted: bool = False
    # ── Crowd suggestion (src/trigger/suggested_clips.py) ────────────────────
    # A moment a spike in audience interest surfaced, without consulting our
    # score. trigger_score/virality_score stay 0.0 on these and that is not
    # missing data, it is the point of the feature.
    #
    # THERE IS NO `suggested_by`. It held the Twitch display name of the viewer
    # who made the clip — a person who is not our user — and it was removed on
    # 2026-08-27 because nothing read it: the UI stopped showing the clipper,
    # and no logic ever consulted it. Storing a third party's name that nothing
    # uses is a disclosure obligation bought for free. `clipper_count` is a
    # count, not an identity, and stays.
    suggested: bool = False
    clipper_count: int = 0        # distinct viewers who clipped this moment
    suggested_views: int = 0      # view count when we surfaced it

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "channel": self.channel,
            "platform": self.platform,
            "trigger_score": self.trigger_score,
            "trigger_signals": self.trigger_signals,
            "chat_snapshot": self.chat_snapshot,
            "stream_title": self.stream_title,
            "game": self.game,
            "created_at": self.created_at,
            "storage_url": self.storage_url,
            "duration_seconds": self.duration_seconds,
            "status": self.status,
            "virality_score": self.virality_score,
            "clip_title": self.clip_title,
            "vertical_url": self.vertical_url,
            "user_id": self.user_id,
            "twitch_clip_id": self.twitch_clip_id,
            "twitch_url": self.twitch_url,
            "embed_url": self.embed_url,
            "thumbnail_url": self.thumbnail_url,
            "age_restricted": self.age_restricted,
            "suggested": self.suggested,
            "clipper_count": self.clipper_count,
            "suggested_views": self.suggested_views,
        }
