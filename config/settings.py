import logging
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

    # FFmpeg / streamlink
    ffmpeg_path: str = "ffmpeg"
    streamlink_path: str = "streamlink"

    # App behaviour
    log_level: str = "INFO"
    dashboard_secret_key: str = _DEFAULT_SECRET
    dashboard_password: str = _DEFAULT_PASSWORD
    dashboard_https_only: bool = True
    buffer_duration_seconds: int = 90
    clip_pre_roll_seconds: int = 30
    clip_post_roll_seconds: int = 10
    max_concurrent_streams: int = 20

    # Twitch OAuth (primary login + per-user clip creation)
    twitch_redirect_uri: str = "https://highlightz.app/auth/twitch/callback"
    admin_twitch_id: str = "593525174"  # Your Twitch user ID — auto-grants admin on login

    # Detection
    enable_audio_detection: bool = True  # pull audio-only feed for the audio-spike signal

    # Stripe billing
    stripe_secret_key: str = ""
    stripe_publishable_key: str = ""
    stripe_price_id: str = ""          # recurring Price ID from Stripe dashboard
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
            # Generate ephemeral random key so at least cookie forgery is prevented
            # at runtime (sessions won't persist across restarts)
            object.__setattr__(self, "dashboard_secret_key", _secrets.token_hex(32))
        if self.dashboard_password == _DEFAULT_PASSWORD:
            _log.critical(
                "SECURITY: DASHBOARD_PASSWORD is using the default value 'highlightz'. "
                "Change it in .env immediately."
            )
        return self


settings = Settings()
