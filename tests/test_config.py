"""Tests for environment configuration."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from melody.config import Settings


def _minimal_env(**overrides: object) -> dict[str, str]:
    base = {
        "SUBSONIC_URL": "http://localhost:4533",
        "SUBSONIC_USERNAME": "user",
        "SUBSONIC_PASSWORD": "pass",
        "MUMBLE_HOST": "localhost",
        "MUMBLE_USERNAME": "Melody",
    }
    base.update({k: str(v) for k, v in overrides.items()})
    return base


def test_search_weights_default() -> None:
    settings = Settings(**_minimal_env())  # type: ignore[arg-type]
    assert settings.search_relevance_percent == 85
    assert settings.search_popularity_percent == 15


def test_search_weights_custom() -> None:
    settings = Settings(
        **_minimal_env(SEARCH_RELEVANCE_PERCENT=70, SEARCH_POPULARITY_PERCENT=30),
    )  # type: ignore[arg-type]
    assert settings.search_relevance_percent == 70
    assert settings.search_popularity_percent == 30


def test_search_weights_must_sum_to_100() -> None:
    with pytest.raises(ValidationError, match="must sum to 100"):
        Settings(
            **_minimal_env(SEARCH_RELEVANCE_PERCENT=80, SEARCH_POPULARITY_PERCENT=10),
        )  # type: ignore[arg-type]
