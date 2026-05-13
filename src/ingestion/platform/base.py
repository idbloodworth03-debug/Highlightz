from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StreamInfo:
    channel: str
    platform: str
    stream_url: str          # HLS/DASH URL for streamlink/ffmpeg
    chat_channel_id: str     # platform-specific chat identifier
    title: str = ""
    game: str = ""
    viewer_count: int = 0


class BasePlatform(ABC):
    """Resolves a channel name to a live StreamInfo."""

    @abstractmethod
    async def get_stream_info(self, channel: str) -> StreamInfo:
        ...

    @abstractmethod
    async def is_live(self, channel: str) -> bool:
        ...
