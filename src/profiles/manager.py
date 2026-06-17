"""
ProfileManager — loads and saves StreamerProfile JSON files.
Supports per-user profile isolation: each user's profiles are seeded
fresh from seed_profiles/ and stored under clips/profiles/{user_id}/.
"""

import json
import asyncio
import os
import tempfile
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
        p = (self._profiles_dir / f"{channel.lower()}.json").resolve()
        if not p.is_relative_to(self._profiles_dir.resolve()):
            raise ValueError(f"Invalid channel for profile path: {channel!r}")
        return p

    def _seed_path(self, channel: str) -> Path:
        p = (_SEED_DIR / f"{channel.lower()}.json").resolve()
        if not p.is_relative_to(_SEED_DIR.resolve()):
            raise ValueError(f"Invalid channel for seed path: {channel!r}")
        return p

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
            data = json.dumps(profile.to_dict(), indent=2)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            try:
                os.write(fd, data.encode("utf-8"))
                os.close(fd)
                os.replace(tmp, path)
            except Exception:
                os.close(fd)
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    async def get(self, channel: str) -> StreamerProfile | None:
        return self._cache.get(channel)

    async def all_profiles(self) -> list[StreamerProfile]:
        async with self._lock:
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

# Per-user profile managers — keyed by user_id.
# Bounded to avoid unbounded growth on long-running servers with many users.
_user_profile_managers: dict[str, ProfileManager] = {}
_MAX_USER_MANAGERS = 500


def get_profile_manager(user_id: str | None) -> ProfileManager:
    """Return the ProfileManager for a given user, creating it if needed."""
    if not user_id:
        return profile_manager
    if user_id not in _user_profile_managers:
        if len(_user_profile_managers) >= _MAX_USER_MANAGERS:
            # Evict the oldest entry (dict is insertion-ordered in Python 3.7+)
            oldest = next(iter(_user_profile_managers))
            del _user_profile_managers[oldest]
        _user_profile_managers[user_id] = ProfileManager(user_id=user_id)
    return _user_profile_managers[user_id]
