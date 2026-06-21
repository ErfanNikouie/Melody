"""Application configuration via environment variables."""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from melody.models import PlayerMode


class Settings(BaseSettings):
    """Melody configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Subsonic
    subsonic_url: str = Field(alias="SUBSONIC_URL")
    subsonic_username: str = Field(alias="SUBSONIC_USERNAME")
    subsonic_password: str = Field(alias="SUBSONIC_PASSWORD")

    # Commands
    command_prefixes: str = Field(default="m/,melody/,/", alias="COMMAND_PREFIXES")

    # Playback / buffering
    disconnect_grace_period: float = Field(default=300.0, alias="DISCONNECT_GRACE_PERIOD")
    audio_buffer_max_mb: int = Field(default=256, alias="AUDIO_BUFFER_MAX_MB")
    audio_buffer_start_seconds: float = Field(default=3.0, alias="AUDIO_BUFFER_START_SECONDS")

    # Logging
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    # Mumble
    mumble_host: str = Field(alias="MUMBLE_HOST")
    mumble_port: int = Field(default=64738, alias="MUMBLE_PORT")
    mumble_username: str = Field(alias="MUMBLE_USERNAME")
    mumble_password: str = Field(default="", alias="MUMBLE_PASSWORD")
    mumble_tls: bool = Field(default=False, alias="MUMBLE_TLS")

    # MelodyPlayer pool
    player_mode: str = Field(default="pool", alias="PLAYER_MODE")
    player_pool_size: int = Field(default=5, alias="PLAYER_POOL_SIZE")
    player_username_prefix: str = Field(default="MelodyPlayer", alias="PLAYER_USERNAME_PREFIX")
    player_password: str = Field(default="", alias="PLAYER_PASSWORD")
    coordinator_accept_root_messages: bool = Field(default=True, alias="COORDINATOR_ACCEPT_ROOT_MESSAGES")

    @field_validator("player_mode")
    @classmethod
    def normalize_player_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ("pool", "per_channel"):
            raise ValueError("PLAYER_MODE must be 'pool' or 'per_channel'")
        return normalized

    @property
    def player_mode_enum(self) -> PlayerMode:
        return PlayerMode.PER_CHANNEL if self.player_mode == "per_channel" else PlayerMode.POOL

    @field_validator("subsonic_url")
    @classmethod
    def normalize_subsonic_url(cls, value: str) -> str:
        url = value.strip().rstrip("/")
        if "://" not in url:
            raise ValueError("SUBSONIC_URL must include a scheme, e.g. http://host.docker.internal:5274")
        return url

    @property
    def prefixes(self) -> list[str]:
        """Return command prefixes sorted longest-first for greedy matching."""
        parts = [p.strip() for p in self.command_prefixes.split(",") if p.strip()]
        return sorted(parts, key=len, reverse=True)

    @property
    def audio_buffer_max_bytes(self) -> int:
        return self.audio_buffer_max_mb * 1024 * 1024


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
