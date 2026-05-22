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
    dashboard_https_only: bool = False
    buffer_duration_seconds: int = 90
    clip_pre_roll_seconds: int = 30
    clip_post_roll_seconds: int = 10
    max_concurrent_streams: int = 20

    # Trigger thresholds
    chat_velocity_multiplier: float = 2.5
    trigger_score_threshold: float = 0.45
    keyword_score_weight: float = 0.35
    velocity_score_weight: float = 0.35
    sentiment_score_weight: float = 0.15
    audio_score_weight: float = 0.15

    @model_validator(mode="after")
    def warn_insecure_defaults(self) -> "Settings":
        if self.dashboard_secret_key == _DEFAULT_SECRET:
            _log.warning("SECURITY: DASHBOARD_SECRET_KEY is using the default value — set it in .env")
        if self.dashboard_password == _DEFAULT_PASSWORD:
            _log.warning("SECURITY: DASHBOARD_PASSWORD is using the default value — set it in .env")
        return self


settings = Settings()
