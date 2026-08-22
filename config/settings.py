import logging
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from typing import Literal

_log = logging.getLogger(__name__)

_DEFAULT_SECRET = "change_me"
_DEFAULT_PASSWORD = "highlightz"


# ── HOW MANY STREAMS THIS BOX CAN CARRY ──────────────────────────────────────
# LIVE streams. This distinction is the whole point and it was wrong for a long
# time: the ceiling used to count REGISTERED channels, which cost almost
# nothing. A channel whose streamer is offline sits in a loop asking "are you
# live yet?" every 30 seconds — no streamlink, no ffmpeg, no chat socket. And
# offline is the normal state: people queue up an evening's roster in advance,
# which stream_worker's own comments call "the normal case". Prod proved it —
# 8 channels registered, load average 0.00, 95% idle. Users were being refused
# on the strength of channels that were costing nothing at all.
#
# Every LIVE stream spawns TWO OS subprocesses, a streamlink pulling an
# audio-only rendition and an ffmpeg decoding it to raw PCM (see
# src/ingestion/audio_meter.py). They are separate processes, so the kernel
# spreads them over every core the machine has — which is why adding vCPUs
# genuinely raises this ceiling rather than doing nothing, as it would if the
# cost sat inside one Python event loop.
#
# THE NUMBER IS MEASURED, NOT CHOSEN. On the original 1-vCPU droplet, eight
# live streams sat at 93.4% of a single core. That is saturation, not capacity:
# it leaves nothing for the web app, the chat sockets or the scoring loop, and
# a box at 100% stops responding rather than degrading. Six per core is that
# measurement with the headroom put back.
#
# Re-measure with scripts/stream_cost.py after any change to the audio meter,
# and set MAX_CONCURRENT_STREAMS explicitly in .env if the derived number is
# ever wrong for a particular box.
_STREAMS_PER_CPU = 6
_MIN_CONCURRENT_STREAMS = 6      # never derive a ceiling below one core's worth
_MAX_CONCURRENT_STREAMS = 400    # a nonsense cpu count must not uncap the box

# How many channels may be REGISTERED per live slot. Registration is nearly
# free, so this is deliberately generous — it exists to stop somebody queueing
# five hundred channels and filling the process, not to ration anything. What
# keeps the box safe when a queued roster all goes live at once is admission
# control at go-live (api.acquire_live_slot), NOT this number.
_REGISTERED_PER_LIVE_SLOT = 4


def _usable_cpus() -> int:
    """Cores this process may actually use.

    os.cpu_count() reports the HOST's cores, which is wrong inside a container
    with a CPU quota — it would hand a 0.5-core container the ceiling of a
    32-core host. The cgroup quota is checked first for that reason. On a plain
    VM (which is what the droplet is) there is no quota and cpu_count is right.
    """
    try:
        with open("/sys/fs/cgroup/cpu.max") as fh:          # cgroup v2
            quota, period = fh.read().split()
            if quota != "max":
                return max(1, int(int(quota) / int(period)))
    except Exception:
        pass
    try:                                                     # cgroup v1
        with open("/sys/fs/cgroup/cpu/cpu.cfs_quota_us") as fh:
            quota = int(fh.read())
        with open("/sys/fs/cgroup/cpu/cpu.cfs_period_us") as fh:
            period = int(fh.read())
        if quota > 0 and period > 0:
            return max(1, quota // period)
    except Exception:
        pass
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except Exception:
        return max(1, os.cpu_count() or 1)


def default_max_concurrent_streams() -> int:
    """The ceiling this machine can carry, derived from its cores.

    Hardcoding it meant the number was only ever right for the box it was
    written on: it sat at 20 on a 1-vCPU droplet that measured out at 6, and
    would have stayed at 20 after an upgrade to eight times the machine. This
    tracks the hardware in both directions."""
    n = _usable_cpus() * _STREAMS_PER_CPU
    return max(_MIN_CONCURRENT_STREAMS, min(_MAX_CONCURRENT_STREAMS, n))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Twitch
    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    twitch_oauth_token: str = ""

    # YouTube
    youtube_api_key: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Storage
    storage_backend: Literal["s3", "gcs", "local"] = "local"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    s3_bucket: str = "superclipbot-clips"
    gcs_project_id: str = ""
    gcs_bucket: str = "superclipbot-clips"
    local_storage_path: str = "./clips"

    # Off-site state backups (src/maintenance/backup.py). Local rotation always
    # runs; upload happens only when a bucket is configured. Endpoint URL makes
    # any S3-compatible store work (DigitalOcean Spaces:
    # https://<region>.digitaloceanspaces.com; credentials via
    # AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY above).
    backup_s3_bucket: str = ""
    backup_s3_endpoint: str = ""
    backup_keep_local: int = 14     # newest N local archives kept by rotation

    # FFmpeg / streamlink
    ffmpeg_path: str = "ffmpeg"
    streamlink_path: str = "streamlink"

    # Clip Upload library. These are the only place in the product where we
    # hold video bytes, so the caps matter: the droplet has a 50 GB disk and a
    # full disk takes down clipping, billing and the dashboard at once, not
    # just uploads. upload_max_total_mb is the real safety net (it bounds the
    # whole feature no matter how many users sign up); the per-user cap is
    # fairness between users, and the per-file cap rejects obvious junk early.
    # A 60s 1080p60 Twitch clip is ~50-100 MB, so 300 MB is generous per file.
    upload_max_file_mb: int = 300
    upload_max_user_mb: int = 2048       # 2 GB per user
    upload_max_total_mb: int = 25600     # 25 GB across all users

    # Release flag. Clip Upload is built and tested but held back until the
    # editing/publishing half exists — shipping "upload a file, then nothing"
    # is worse than not shipping it. Off means users get an under-construction
    # screen AND the API refuses, so a direct POST cannot fill the disk while
    # the tab is hidden. Admins bypass it so the owner can exercise the real
    # feature on prod; their dashboard says plainly that users cannot see it.
    # Flip to true (UPLOADS_ENABLED=true in .env) to launch.
    uploads_enabled: bool = False

    # Importing a user's own Twitch clips is a SEPARATE, already-complete
    # feature: it lists metadata through documented Helix and needs no editor
    # to be useful ("every clip on my channel in one place" is the whole
    # thing). Its own flag so it can ship without the unfinished upload half —
    # the reason uploads are held back does not apply to it.
    clip_import_enabled: bool = False

    # Auto-captions (Whisper, on this box — owner's call over a paid API).
    # 'tiny.en' is the only size that belongs on a 1 vCPU / 2 GB droplet that is
    # already running an audio meter per monitored channel; 'base.en' is roughly
    # 2x the cost for a modest accuracy gain, so move up only if the box is
    # visibly idle. Captioning is deliberately serialised to one clip at a time
    # (src/captions/transcribe.py) — clip detection must always win the core.
    captions_enabled: bool = False
    captions_model: str = "tiny.en"
    captions_timeout_s: float = 240.0
    # Whisper's voice-activity filter drops audio it judges to be non-speech
    # BEFORE transcription, so anything it gets wrong is gone — there is no
    # later stage that can recover it. On a real 30s clip it kept 8s and cut
    # the sentence in half; short-form clips are exactly its weak case (loud
    # game audio and music under the speech). Off by default: transcribing the
    # whole clip costs more CPU but cannot silently lose the second half of
    # what someone said. CAPTIONS_VAD=true to put it back.
    captions_vad: bool = False

    # App behaviour
    log_level: str = "INFO"
    # Bind address for the dashboard server. Nginx proxies via localhost, so
    # 127.0.0.1 keeps uvicorn unreachable from outside even if the firewall is
    # misconfigured. Set DASHBOARD_HOST=0.0.0.0 only when the process itself
    # must accept external connections (e.g. inside a Docker port mapping).
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    dashboard_secret_key: str = _DEFAULT_SECRET
    dashboard_password: str = _DEFAULT_PASSWORD
    dashboard_https_only: bool = True
    # Separate key for Fernet-encrypting Twitch OAuth tokens at rest.
    # Set TOKEN_ENCRYPTION_KEY in .env to a random 32-byte hex string so
    # token encryption is independent of the session signing key.
    # If unset, falls back to deriving from dashboard_secret_key (old behaviour).
    token_encryption_key: str = ""
    buffer_duration_seconds: int = 90
    clip_pre_roll_seconds: int = 30
    clip_post_roll_seconds: int = 10
    # LIVE streams at once. Derived from the machine, overridable with
    # MAX_CONCURRENT_STREAMS in .env. See default_max_concurrent_streams().
    max_concurrent_streams: int = 0
    # REGISTERED channels at once, across the whole process. Derived from the
    # live ceiling; override with MAX_REGISTERED_STREAMS in .env.
    max_registered_streams: int = 0
    # Decode a VOD's audio during a scan so AUDIO_SPIKE — the heaviest non-chat
    # signal the live engine has — contributes to VOD moments too. Off by
    # default because it changes a scan from seconds to minutes and pulls the
    # whole audio track: switch it on deliberately per box.
    vod_audio_enabled: bool = False

    # Twitch OAuth (primary login + per-user clip creation)
    twitch_redirect_uri: str = "https://highlightz.app/auth/twitch/callback"
    # Set ADMIN_TWITCH_ID in .env — do not hard-code a real ID in source.
    admin_twitch_id: str = ""

    # Kick OAuth (secondary platform — link after Twitch login)
    kick_client_id: str = ""
    kick_client_secret: str = ""
    kick_redirect_uri: str = "https://highlightz.app/auth/kick/callback"

    # Detection
    enable_audio_detection: bool = True  # pull audio-only feed for the audio-spike signal

    # Stripe billing
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    # Legacy single-price id ($15 era). Kept so the webhook can recognize old
    # subscriptions (mapped to the 'pro' plan) — do not reuse for new signups.
    stripe_price_id: str = ""
    # Two-tier prices (recurring Price IDs from the Stripe dashboard).
    stripe_price_id_starter: str = ""   # $10/month
    stripe_price_id_pro: str = ""       # $25/month
    stripe_webhook_secret: str = ""

    # Trigger thresholds
    chat_velocity_multiplier: float = 2.5
    trigger_score_threshold: float = 0.45
    keyword_score_weight: float = 0.35
    velocity_score_weight: float = 0.35
    sentiment_score_weight: float = 0.15
    audio_score_weight: float = 0.15

    @model_validator(mode="after")
    def warn_insecure_defaults(self) -> "Settings":
        import secrets as _secrets
        # 0 means "nobody pinned it" — derive from this machine. Done here
        # rather than with a default_factory so an explicit MAX_CONCURRENT_
        # STREAMS in .env still wins, and so the resolved number is logged:
        # a capacity ceiling nobody can see is one nobody will question.
        if self.max_concurrent_streams <= 0:
            object.__setattr__(self, "max_concurrent_streams",
                               default_max_concurrent_streams())
            _log.info(
                "max_concurrent_streams derived from hardware: %d LIVE streams "
                "(%d usable cpu x %d each). Set MAX_CONCURRENT_STREAMS in .env "
                "to pin it.",
                self.max_concurrent_streams, _usable_cpus(), _STREAMS_PER_CPU,
            )
        if self.max_registered_streams <= 0:
            object.__setattr__(
                self, "max_registered_streams",
                self.max_concurrent_streams * _REGISTERED_PER_LIVE_SLOT)
            _log.info(
                "max_registered_streams derived: %d registered channels "
                "(%d live slots x %d). Set MAX_REGISTERED_STREAMS in .env to "
                "pin it.",
                self.max_registered_streams, self.max_concurrent_streams,
                _REGISTERED_PER_LIVE_SLOT,
            )
        if self.dashboard_secret_key == _DEFAULT_SECRET:
            _log.critical(
                "SECURITY: DASHBOARD_SECRET_KEY is using the default value. "
                "Sessions are NOT secure. Set DASHBOARD_SECRET_KEY in .env: "
                "python -c \"import secrets; print(secrets.token_hex(32))\""
            )
            object.__setattr__(self, "dashboard_secret_key", _secrets.token_hex(32))
        if self.dashboard_password == _DEFAULT_PASSWORD:
            _log.critical(
                "SECURITY: DASHBOARD_PASSWORD is using the default value 'highlightz'. "
                "Change it in .env immediately."
            )
        if not self.admin_twitch_id:
            _log.warning(
                "ADMIN_TWITCH_ID not set in .env — no Twitch account will be "
                "auto-granted admin on login. Set ADMIN_TWITCH_ID=<your_twitch_id>."
            )
        # Resolve relative storage path to absolute so path-containment checks
        # are stable regardless of the process working directory.
        if not os.path.isabs(self.local_storage_path):
            object.__setattr__(
                self, "local_storage_path", os.path.abspath(self.local_storage_path)
            )
        return self


settings = Settings()
