"""Application configuration via environment variables."""

from __future__ import annotations

from pydantic import Field, field_validator, model_validator
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

    # Playback
    disconnect_grace_period: float = Field(default=300.0, alias="DISCONNECT_GRACE_PERIOD")
    starting_volume: int = Field(default=100, alias="STARTING_VOLUME")
    ffmpeg_probesize: str = Field(default="32k", alias="FFMPEG_PROBESIZE")
    ffmpeg_analyzeduration: str = Field(default="500k", alias="FFMPEG_ANALYZEDURATION")
    pcm_target_buffer_ms: int = Field(default=80, alias="PCM_TARGET_BUFFER_MS")
    pcm_max_prebuffer_frames: int = Field(default=6, alias="PCM_MAX_PREBUFFER_FRAMES")
    pcm_prebuffer_batch_size: int = Field(default=1, alias="PCM_PREBUFFER_BATCH_SIZE")

    # Search ranking (must sum to 100)
    search_relevance_percent: int = Field(default=85, alias="SEARCH_RELEVANCE_PERCENT")
    search_popularity_percent: int = Field(default=15, alias="SEARCH_POPULARITY_PERCENT")
    search_results_limit: int = Field(default=10, alias="SEARCH_RESULTS_LIMIT")
    list_window_size: int = Field(default=50, alias="LIST_WINDOW_SIZE")

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

    @field_validator("search_relevance_percent", "search_popularity_percent")
    @classmethod
    def validate_search_percent_range(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError("Search ranking percentages must be between 0 and 100")
        return value

    @field_validator("starting_volume")
    @classmethod
    def validate_starting_volume(cls, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError("STARTING_VOLUME must be between 0 and 100")
        return value

    @field_validator("pcm_target_buffer_ms")
    @classmethod
    def validate_pcm_target_buffer_ms(cls, value: int) -> int:
        if not 20 <= value <= 500:
            raise ValueError("PCM_TARGET_BUFFER_MS must be between 20 and 500")
        return value

    @field_validator("pcm_max_prebuffer_frames", "pcm_prebuffer_batch_size")
    @classmethod
    def validate_positive_pcm_int(cls, value: int) -> int:
        if value < 1:
            raise ValueError("PCM prebuffer settings must be at least 1")
        return value

    @field_validator("search_results_limit", "list_window_size")
    @classmethod
    def validate_positive_list_int(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Search/list limits must be at least 1")
        return value

    @model_validator(mode="after")
    def validate_search_percents_sum(self) -> Settings:
        total = self.search_relevance_percent + self.search_popularity_percent
        if total != 100:
            raise ValueError(
                "SEARCH_RELEVANCE_PERCENT and SEARCH_POPULARITY_PERCENT must sum to 100 "
                f"(got {self.search_relevance_percent} + {self.search_popularity_percent} = {total})"
            )
        return self

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


def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
