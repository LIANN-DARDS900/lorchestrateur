"""Environment-backed application configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


class ConfigurationError(ValueError):
    """Raised when configuration is present but invalid."""


def _parse_bool(name: str, raw_value: str) -> bool:
    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be a boolean value")


def _parse_provider_order(raw_value: str) -> tuple[str, ...]:
    providers = tuple(dict.fromkeys(item.strip() for item in raw_value.split(",") if item.strip()))
    if not providers:
        raise ConfigurationError("AI_PROVIDER_ORDER must contain at least one provider name")
    return providers


def _parse_quality_score(name: str, raw_value: str) -> int:
    try:
        value = int(raw_value.strip())
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not 0 <= value <= 100:
        raise ConfigurationError(f"{name} must be between 0 and 100")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Non-secret application settings loaded from an explicit environment mapping."""

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/lorchestrateur.db"
    allow_paid_ai: bool = False
    ai_provider_order: tuple[str, ...] = ("local", "gemini", "openrouter")
    platform_min_quality_score: int = 80

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if environ is None else environ
        app_env = source.get("APP_ENV", "development").strip()
        log_level = source.get("LOG_LEVEL", "INFO").strip().upper()
        database_url = source.get(
            "DATABASE_URL", "sqlite:///./data/lorchestrateur.db"
        ).strip()

        if not app_env:
            raise ConfigurationError("APP_ENV cannot be empty")
        if not database_url:
            raise ConfigurationError("DATABASE_URL cannot be empty")

        return cls(
            app_env=app_env,
            log_level=log_level,
            database_url=database_url,
            allow_paid_ai=_parse_bool(
                "ALLOW_PAID_AI", source.get("ALLOW_PAID_AI", "false")
            ),
            ai_provider_order=_parse_provider_order(
                source.get("AI_PROVIDER_ORDER", "local,gemini,openrouter")
            ),
            platform_min_quality_score=_parse_quality_score(
                "PLATFORM_MIN_QUALITY_SCORE",
                source.get("PLATFORM_MIN_QUALITY_SCORE", "80"),
            ),
        )
