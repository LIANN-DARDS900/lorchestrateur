"""Contracts shared by AI provider adapters and the orchestration layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol


class AITask(StrEnum):
    STRATEGIC_ANGLE = "strategic_angle"
    CONTENT_STRATEGY = "content_strategy"
    MASTER_CONTENT = "master_content"
    PLATFORM_ADAPTATION = "platform_adaptation"
    CONTROLLED_REWRITE = "controlled_rewrite"


class AIOutputSchema(StrEnum):
    CONTENT_STRATEGY_V1 = "content_strategy_v1"
    MASTER_CONTENT_V1 = "master_content_v1"
    BLOG_CONTENT_V1 = "blog_content_v1"
    X_CONTENT_V1 = "x_content_v1"
    INSTAGRAM_CONTENT_V1 = "instagram_content_v1"
    FACEBOOK_CONTENT_V1 = "facebook_content_v1"


class ProviderCostClass(StrEnum):
    """Configured cost governance; unknown is treated as paid by the router policy."""

    FREE = "free"
    PAID = "paid"
    UNKNOWN = "unknown"

    @property
    def requires_paid_authorization(self) -> bool:
        return self is not ProviderCostClass.FREE


@dataclass(frozen=True, slots=True)
class AIUsage:
    """Optional, non-content usage metadata returned by a provider."""

    requested_at: datetime
    latency_ms: int
    retry_count: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    estimated_cost: float | None = None
    cost_class: ProviderCostClass = ProviderCostClass.UNKNOWN

    def __post_init__(self) -> None:
        if self.requested_at.tzinfo is None or self.requested_at.utcoffset() is None:
            raise ValueError("requested_at must include timezone information")
        for name in ("latency_ms", "retry_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer when provided")
        if self.estimated_cost is not None:
            if not isinstance(self.estimated_cost, (int, float)) or isinstance(
                self.estimated_cost, bool
            ):
                raise ValueError("estimated_cost must be numeric when provided")
            if self.estimated_cost < 0:
                raise ValueError("estimated_cost cannot be negative")
        if not isinstance(self.cost_class, ProviderCostClass):
            raise ValueError("cost_class must be a ProviderCostClass")

    def trace_metadata(self) -> Mapping[str, Any]:
        values: dict[str, Any] = {
            "request_timestamp": self.requested_at.isoformat(),
            "provider_latency_ms": self.latency_ms,
            "retry_count": self.retry_count,
            "cost_class": self.cost_class.value,
        }
        for key in ("input_tokens", "output_tokens", "total_tokens", "estimated_cost"):
            value = getattr(self, key)
            if value is not None:
                values[key] = value
        return MappingProxyType(values)


@dataclass(frozen=True, slots=True)
class AIRequest:
    task: AITask
    prompt: str
    context: Mapping[str, Any]
    max_output_characters: int = 10_000
    output_schema: AIOutputSchema | None = None
    response_json_schema: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task, AITask):
            raise ValueError("AI request task must be an AITask")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("AI request prompt cannot be empty")
        if not isinstance(self.context, Mapping):
            raise ValueError("AI request context must be a mapping")
        if not isinstance(self.max_output_characters, int) or isinstance(
            self.max_output_characters, bool
        ):
            raise ValueError("max_output_characters must be an integer")
        if self.max_output_characters <= 0:
            raise ValueError("max_output_characters must be positive")
        if self.output_schema is not None and not isinstance(
            self.output_schema, AIOutputSchema
        ):
            raise ValueError("output_schema must be an AIOutputSchema")
        if self.response_json_schema is not None and not isinstance(
            self.response_json_schema, Mapping
        ):
            raise ValueError("response_json_schema must be a mapping when provided")
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))
        if self.response_json_schema is not None:
            object.__setattr__(
                self,
                "response_json_schema",
                MappingProxyType(dict(self.response_json_schema)),
            )


@dataclass(frozen=True, slots=True)
class AIResponse:
    content: str
    provider: str
    model: str
    metadata: Mapping[str, Any]
    structured_output: Mapping[str, Any] | None = None
    usage: AIUsage | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise ValueError("AI response content must be a string")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("AI response provider cannot be empty")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("AI response model cannot be empty")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("AI response metadata must be a mapping")
        if self.structured_output is not None and not isinstance(
            self.structured_output, Mapping
        ):
            raise ValueError("structured_output must be a mapping when provided")
        if self.usage is not None and not isinstance(self.usage, AIUsage):
            raise ValueError("usage must be AIUsage when provided")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.structured_output is not None:
            object.__setattr__(
                self,
                "structured_output",
                MappingProxyType(dict(self.structured_output)),
            )

    def trace_metadata(self) -> Mapping[str, Any]:
        """Return the intentionally small, content-free subset allowed in generic traces."""

        values: dict[str, Any] = {"provider": self.provider, "model": self.model}
        if self.usage is not None:
            values.update(self.usage.trace_metadata())
        return MappingProxyType(values)


class AIProviderError(RuntimeError):
    """Sanitized provider failure that permits routing to another provider."""

    classification = "provider_error"
    retryable = False

    def __init__(self, message: str, *, retry_count: int = 0) -> None:
        self.retry_count = retry_count
        super().__init__(message)


class ProviderConfigurationError(AIProviderError):
    classification = "not_configured"


class ProviderAuthenticationError(AIProviderError):
    classification = "authentication_error"


class ProviderRateLimitError(AIProviderError):
    classification = "rate_limited"
    retryable = True

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message)


class ProviderTimeoutError(AIProviderError):
    classification = "timeout"
    retryable = True


class ProviderTransientError(AIProviderError):
    classification = "transient_error"
    retryable = True


class ProviderPermanentError(AIProviderError):
    classification = "permanent_error"


class ProviderResponseError(AIProviderError):
    classification = "malformed_response"


class AIProvider(Protocol):
    """Minimal interface implemented by local, hosted, and test providers."""

    @property
    def name(self) -> str: ...

    @property
    def is_configured(self) -> bool: ...

    @property
    def is_paid(self) -> bool: ...

    def is_available(self) -> bool: ...

    def generate(self, request: AIRequest) -> AIResponse: ...
