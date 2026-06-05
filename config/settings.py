from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Literal


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Twitch
    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    twitch_oauth_token: str = ""

    # YouTube
    youtube_api_key: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://superclip:password@localhost:5432/superclipbot"

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
    environment: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    dashboard_secret_key: str = "change_me"
    dashboard_password: str = "highlightz"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8000
    # Send session cookie only over HTTPS. Keep False for local http dev.
    session_cookie_secure: bool = False
    # Comma-separated list of allowed CORS origins. Empty = same-origin only.
    cors_origins: str = ""
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

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def validate_for_production(self) -> None:
        """Refuse to boot in production with placeholder secrets.

        Called from the app entry point (not at import time) so tests and
        local development still work without a populated .env.
        """
        if self.environment != "production":
            return

        insecure_keys = {
            "", "change_me", "change_me_in_production",
            "change_this_to_a_random_secret_key",
        }
        insecure_passwords = {"", "highlightz", "change_this_password"}
        problems: list[str] = []
        if self.dashboard_secret_key in insecure_keys or len(self.dashboard_secret_key) < 16:
            problems.append(
                "DASHBOARD_SECRET_KEY is unset or a default. Generate one with "
                "`python3 -c \"import secrets; print(secrets.token_hex(32))\"`."
            )
        if self.dashboard_password in insecure_passwords:
            problems.append("DASHBOARD_PASSWORD is unset or a default. Set a strong password.")
        if problems:
            raise RuntimeError(
                "Refusing to start in production with insecure config:\n  - "
                + "\n  - ".join(problems)
            )


settings = Settings()
