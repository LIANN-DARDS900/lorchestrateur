"""Environment-backed application configuration."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lorchestrateur.ai.contracts import ProviderCostClass


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


def _parse_timeout(name: str, raw_value: str) -> float:
    try:
        value = float(raw_value.strip())
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be numeric") from exc
    if not 0 < value <= 300:
        raise ConfigurationError(f"{name} must be between 0 and 300 seconds")
    return value


def _parse_retries(name: str, raw_value: str) -> int:
    try:
        value = int(raw_value.strip())
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not 0 <= value <= 5:
        raise ConfigurationError(f"{name} must be between 0 and 5")
    return value


def _parse_positive_int(name: str, raw_value: str, *, maximum: int) -> int:
    try:
        value = int(raw_value.strip())
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise ConfigurationError(f"{name} must be between 1 and {maximum}")
    return value


def _parse_timezone(name: str, raw_value: str) -> str:
    value = raw_value.strip()
    try:
        ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ConfigurationError(f"{name} must be a valid IANA timezone") from exc
    return value


def _parse_cost_class(name: str, raw_value: str) -> ProviderCostClass:
    try:
        return ProviderCostClass(raw_value.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in ProviderCostClass)
        raise ConfigurationError(f"{name} must be one of: {allowed}") from exc


def _optional_value(raw_value: str | None) -> str | None:
    normalized = raw_value.strip() if raw_value else ""
    return normalized or None


def _parse_base_url(name: str, raw_value: str) -> str:
    value = raw_value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} must be an HTTPS URL without query or fragment")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime settings; secret fields are excluded from the dataclass representation."""

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite:///./data/lorchestrateur.db"
    allow_paid_ai: bool = False
    ai_provider_order: tuple[str, ...] = ("local", "gemini", "openrouter")
    platform_min_quality_score: int = 80
    app_ai_mode: str = "demo"
    web_secret_key: str | None = field(default=None, repr=False)
    web_host: str = "127.0.0.1"
    web_port: int = 5000
    gemini_api_key: str | None = field(default=None, repr=False)
    gemini_model: str = ""
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_seconds: float = 30.0
    gemini_max_retries: int = 2
    gemini_cost_class: ProviderCostClass = ProviderCostClass.UNKNOWN
    gemini_enabled: bool = True
    openrouter_api_key: str | None = field(default=None, repr=False)
    openrouter_model: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = 30.0
    openrouter_max_retries: int = 2
    openrouter_cost_class: ProviderCostClass = ProviderCostClass.UNKNOWN
    openrouter_enabled: bool = True
    publishing_enabled: bool = False
    publishing_dry_run: bool = True
    publishing_adapter_mode: str = "demo"
    publishing_max_retries: int = 2
    publishing_lease_seconds: int = 120
    publishing_poll_seconds: int = 10
    app_timezone: str = "Africa/Casablanca"
    x_publishing_enabled: bool = False
    x_access_token: str | None = field(default=None, repr=False)
    x_api_base_url: str = "https://api.x.com"
    facebook_publishing_enabled: bool = False
    meta_page_access_token: str | None = field(default=None, repr=False)
    meta_page_id: str | None = None
    meta_graph_base_url: str = "https://graph.facebook.com/v23.0"
    instagram_publishing_enabled: bool = False
    instagram_access_token: str | None = field(default=None, repr=False)
    instagram_business_account_id: str | None = None
    blog_publishing_enabled: bool = False
    blog_export_directory: str = "./data/exports"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if environ is None else environ
        app_env = source.get("APP_ENV", "development").strip()
        log_level = source.get("LOG_LEVEL", "INFO").strip().upper()
        database_url = source.get("DATABASE_URL", "sqlite:///./data/lorchestrateur.db").strip()

        if not app_env:
            raise ConfigurationError("APP_ENV cannot be empty")
        if not database_url:
            raise ConfigurationError("DATABASE_URL cannot be empty")
        app_ai_mode = source.get("APP_AI_MODE", "demo").strip().lower()
        if app_ai_mode not in {"demo", "real"}:
            raise ConfigurationError("APP_AI_MODE must be demo or real")
        publishing_adapter_mode = source.get("PUBLISHING_ADAPTER_MODE", "demo").strip().lower()
        if publishing_adapter_mode not in {"demo", "real"}:
            raise ConfigurationError("PUBLISHING_ADAPTER_MODE must be demo or real")
        web_host = source.get("WEB_HOST", "127.0.0.1").strip()
        if not web_host:
            raise ConfigurationError("WEB_HOST cannot be empty")
        try:
            web_port = int(source.get("WEB_PORT", "5000").strip())
        except ValueError as exc:
            raise ConfigurationError("WEB_PORT must be an integer") from exc
        if not 1 <= web_port <= 65535:
            raise ConfigurationError("WEB_PORT must be between 1 and 65535")
        blog_export_directory = source.get("BLOG_EXPORT_DIRECTORY", "./data/exports").strip()
        if not blog_export_directory:
            raise ConfigurationError("BLOG_EXPORT_DIRECTORY cannot be empty")

        return cls(
            app_env=app_env,
            log_level=log_level,
            database_url=database_url,
            allow_paid_ai=_parse_bool("ALLOW_PAID_AI", source.get("ALLOW_PAID_AI", "false")),
            ai_provider_order=_parse_provider_order(
                source.get("AI_PROVIDER_ORDER", "local,gemini,openrouter")
            ),
            platform_min_quality_score=_parse_quality_score(
                "PLATFORM_MIN_QUALITY_SCORE",
                source.get("PLATFORM_MIN_QUALITY_SCORE", "80"),
            ),
            app_ai_mode=app_ai_mode,
            web_secret_key=_optional_value(source.get("WEB_SECRET_KEY")),
            web_host=web_host,
            web_port=web_port,
            gemini_api_key=_optional_value(source.get("GEMINI_API_KEY")),
            gemini_model=source.get("GEMINI_MODEL", "").strip(),
            gemini_base_url=_parse_base_url(
                "GEMINI_BASE_URL",
                source.get(
                    "GEMINI_BASE_URL",
                    "https://generativelanguage.googleapis.com/v1beta",
                ),
            ),
            gemini_timeout_seconds=_parse_timeout(
                "GEMINI_TIMEOUT_SECONDS", source.get("GEMINI_TIMEOUT_SECONDS", "30")
            ),
            gemini_max_retries=_parse_retries(
                "GEMINI_MAX_RETRIES", source.get("GEMINI_MAX_RETRIES", "2")
            ),
            gemini_cost_class=_parse_cost_class(
                "GEMINI_COST_CLASS", source.get("GEMINI_COST_CLASS", "unknown")
            ),
            gemini_enabled=_parse_bool("GEMINI_ENABLED", source.get("GEMINI_ENABLED", "true")),
            openrouter_api_key=_optional_value(source.get("OPENROUTER_API_KEY")),
            openrouter_model=source.get("OPENROUTER_MODEL", "").strip(),
            openrouter_base_url=_parse_base_url(
                "OPENROUTER_BASE_URL",
                source.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            ),
            openrouter_timeout_seconds=_parse_timeout(
                "OPENROUTER_TIMEOUT_SECONDS",
                source.get("OPENROUTER_TIMEOUT_SECONDS", "30"),
            ),
            openrouter_max_retries=_parse_retries(
                "OPENROUTER_MAX_RETRIES", source.get("OPENROUTER_MAX_RETRIES", "2")
            ),
            openrouter_cost_class=_parse_cost_class(
                "OPENROUTER_COST_CLASS",
                source.get("OPENROUTER_COST_CLASS", "unknown"),
            ),
            openrouter_enabled=_parse_bool(
                "OPENROUTER_ENABLED", source.get("OPENROUTER_ENABLED", "true")
            ),
            publishing_enabled=_parse_bool(
                "PUBLISHING_ENABLED", source.get("PUBLISHING_ENABLED", "false")
            ),
            publishing_dry_run=_parse_bool(
                "PUBLISHING_DRY_RUN", source.get("PUBLISHING_DRY_RUN", "true")
            ),
            publishing_adapter_mode=publishing_adapter_mode,
            publishing_max_retries=_parse_retries(
                "PUBLISHING_MAX_RETRIES", source.get("PUBLISHING_MAX_RETRIES", "2")
            ),
            publishing_lease_seconds=_parse_positive_int(
                "PUBLISHING_LEASE_SECONDS",
                source.get("PUBLISHING_LEASE_SECONDS", "120"),
                maximum=3600,
            ),
            publishing_poll_seconds=_parse_positive_int(
                "PUBLISHING_POLL_SECONDS",
                source.get("PUBLISHING_POLL_SECONDS", "10"),
                maximum=300,
            ),
            app_timezone=_parse_timezone(
                "APP_TIMEZONE", source.get("APP_TIMEZONE", "Africa/Casablanca")
            ),
            x_publishing_enabled=_parse_bool(
                "X_PUBLISHING_ENABLED", source.get("X_PUBLISHING_ENABLED", "false")
            ),
            x_access_token=_optional_value(source.get("X_ACCESS_TOKEN")),
            x_api_base_url=_parse_base_url(
                "X_API_BASE_URL", source.get("X_API_BASE_URL", "https://api.x.com")
            ),
            facebook_publishing_enabled=_parse_bool(
                "FACEBOOK_PUBLISHING_ENABLED",
                source.get("FACEBOOK_PUBLISHING_ENABLED", "false"),
            ),
            meta_page_access_token=_optional_value(source.get("META_PAGE_ACCESS_TOKEN")),
            meta_page_id=_optional_value(source.get("META_PAGE_ID")),
            meta_graph_base_url=_parse_base_url(
                "META_GRAPH_BASE_URL",
                source.get("META_GRAPH_BASE_URL", "https://graph.facebook.com/v23.0"),
            ),
            instagram_publishing_enabled=_parse_bool(
                "INSTAGRAM_PUBLISHING_ENABLED",
                source.get("INSTAGRAM_PUBLISHING_ENABLED", "false"),
            ),
            instagram_access_token=_optional_value(source.get("INSTAGRAM_ACCESS_TOKEN")),
            instagram_business_account_id=_optional_value(
                source.get("INSTAGRAM_BUSINESS_ACCOUNT_ID")
            ),
            blog_publishing_enabled=_parse_bool(
                "BLOG_PUBLISHING_ENABLED",
                source.get("BLOG_PUBLISHING_ENABLED", "false"),
            ),
            blog_export_directory=blog_export_directory,
        )
