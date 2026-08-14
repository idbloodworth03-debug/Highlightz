from abc import ABC, abstractmethod
from dataclasses import dataclass


class ChannelOffline(ValueError):
    """The channel exists but is not broadcasting right now.

    NOT an error. Most monitored channels are offline most of the time, and a
    clipper queueing up tomorrow's roster adds every one of them offline — it is
    the single most common outcome in the whole system.

    It had no type of its own, so it came through as a bare ValueError and got
    treated as a failure three times over: tenacity retried it (three Helix
    calls to be told the same thing), the worker logged a full traceback at
    ERROR, and the user was shown "a stream hit an error". Four offline channels
    produced a scary toast every ~34 seconds each and roughly 25MB of journal
    a day, none of which described a real problem.

    Subclasses ValueError deliberately: every platform's is_live() already does
    `except ValueError`, and so do callers outside this package. Narrowing the
    type must not change what any of them catch.
    """


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
