import logging
import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator
from typing import Literal

_log = logging.getLogger(__name__)

_DEFAULT_SECRET = "change_me"
_DEFAULT_PASSWORD = "highlightz"


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
    max_concurrent_streams: int = 20

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
