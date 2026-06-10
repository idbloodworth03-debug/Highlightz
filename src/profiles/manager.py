"""
ProfileManager — loads and saves StreamerProfile JSON files.
Supports per-user profile isolation: each user's profiles are seeded
fresh from seed_profiles/ and stored under clips/profiles/{user_id}/.
"""

import json
import asyncio
import structlog
from pathlib import Path

from config.settings import settings
from .profile import StreamerProfile

log = structlog.get_logger(__name__)

_BASE_PROFILES_DIR = Path(settings.local_storage_path) / "profiles"
_SEED_DIR = Path(__file__).parent.parent.parent / "seed_profiles"


class ProfileManager:
    def __init__(self, user_id: str | None = None) -> None:
        # Per-user isolation: clips/profiles/{user_id}/ or global clips/profiles/
        if user_id:
            self._profiles_dir = Path(settings.local_storage_path) / "profiles" / user_id
        else:
            self._profiles_dir = _BASE_PROFILES_DIR
        self._profiles_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, StreamerProfile] = {}
        self._lock = asyncio.Lock()
        self._user_id = user_id

    def _path(self, channel: str) -> Path:
        return self._profiles_dir / f"{channel.lower()}.json"

    def _seed_path(self, channel: str) -> Path:
        return _SEED_DIR / f"{channel.lower()}.json"

    async def load(self, channel: str, platform: str = "twitch") -> StreamerProfile:
        async with self._lock:
            if channel in self._cache:
                return self._cache[channel]

            path = self._path(channel)

            # Always seed fresh from seed_profiles/ if no runtime profile exists yet
            if not path.exists():
                seed = self._seed_path(channel)
                if seed.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(seed.read_text(encoding="utf-8"), encoding="utf-8")
                    log.info("profile_seeded", channel=channel, user=self._user_id,
                             seed=str(seed))

            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    profile = StreamerProfile.from_dict(data)
                    log.info("profile_loaded", channel=channel, user=self._user_id,
                             threshold=profile.trigger_threshold)
                except Exception as exc:
                    log.warning("profile_load_failed", channel=channel, error=str(exc))
                    profile = StreamerProfile(channel=channel, platform=platform)
            else:
                profile = StreamerProfile(channel=channel, platform=platform)
                log.info("profile_created", channel=channel, user=self._user_id)

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
        for path in self._profiles_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                profiles.append(StreamerProfile.from_dict(data))
            except Exception:
                pass
        return profiles[:200]


# Global profile manager (no user isolation) — kept for backwards compat
profile_manager = ProfileManager()

# Per-user profile managers — keyed by user_id
_user_profile_managers: dict[str, ProfileManager] = {}


def get_profile_manager(user_id: str | None) -> ProfileManager:
    """Return the ProfileManager for a given user, creating it if needed."""
    if not user_id:
        return profile_manager
    if user_id not in _user_profile_managers:
        _user_profile_managers[user_id] = ProfileManager(user_id=user_id)
    return _user_profile_managers[user_id]
