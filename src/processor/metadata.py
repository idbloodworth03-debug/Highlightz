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
        }
