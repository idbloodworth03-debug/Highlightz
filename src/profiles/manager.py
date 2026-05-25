"""
ProfileManager — loads and saves StreamerProfile JSON files.
Thread-safe for async use (single event loop).
"""

import json
import asyncio
import structlog
from pathlib import Path

from config.settings import settings
from .profile import StreamerProfile

log = structlog.get_logger(__name__)

_PROFILES_DIR = Path(settings.local_storage_path) / "profiles"
_SEED_DIR = Path("seed_profiles")


class ProfileManager:
    def __init__(self) -> None:
        _PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, StreamerProfile] = {}
        self._lock = asyncio.Lock()

    def _path(self, channel: str) -> Path:
        return _PROFILES_DIR / f"{channel.lower()}.json"

    def _seed_path(self, channel: str) -> Path:
        return _SEED_DIR / f"{channel.lower()}.json"

    async def load(self, channel: str, platform: str = "twitch") -> StreamerProfile:
        async with self._lock:
            if channel in self._cache:
                return self._cache[channel]

            path = self._path(channel)

            # Auto-seed from seed_profiles/ if no runtime profile exists yet
            if not path.exists():
                seed = self._seed_path(channel)
                if seed.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
                    log.info("profile_seeded", channel=channel, seed=str(seed))

            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    profile = StreamerProfile.from_dict(data)
                    log.info("profile_loaded", channel=channel, clips=profile.total_clips,
                             threshold=profile.trigger_threshold)
                except Exception as exc:
                    log.warning("profile_load_failed", channel=channel, error=str(exc))
                    profile = StreamerProfile(channel=channel, platform=platform)
            else:
                profile = StreamerProfile(channel=channel, platform=platform)
                log.info("profile_created", channel=channel)

            self._cache[channel] = profile
            return profile

    async def save(self, profile: StreamerProfile) -> None:
        async with self._lock:
            self._cache[profile.channel] = profile
            path = self._path(profile.channel)
            path.write_text(
                json.dumps(profile.to_dict(), indent=2),
                encoding="utf-8",
            )

    async def get(self, channel: str) -> StreamerProfile | None:
        return self._cache.get(channel)

    async def all_profiles(self) -> list[StreamerProfile]:
        profiles = []
        for path in _PROFILES_DIR.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                profiles.append(StreamerProfile.from_dict(data))
            except Exception:
                pass
        return profiles


# Singleton used across the app
profile_manager = ProfileManager()
