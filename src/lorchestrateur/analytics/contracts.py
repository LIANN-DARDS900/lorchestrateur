"""Stable analytics adapter contracts and sanitized failure classes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from lorchestrateur.domain.analytics import MetricDefinition
from lorchestrateur.domain.publication import PublicationReceipt


class AnalyticsError(RuntimeError):
    classification = "analytics_error"
    retryable = False


class AnalyticsAuthenticationError(AnalyticsError):
    classification = "authentication"


class AnalyticsPermissionError(AnalyticsError):
    classification = "permission"


class AnalyticsRateLimitError(AnalyticsError):
    classification = "rate_limit"
    retryable = True

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class AnalyticsUnavailableMetricError(AnalyticsError):
    classification = "metric_unavailable"


class AnalyticsTransientError(AnalyticsError):
    classification = "transient"
    retryable = True


class AnalyticsPermanentError(AnalyticsError):
    classification = "permanent"


class AnalyticsResponseError(AnalyticsError):
    classification = "response"


class AnalyticsUnavailableError(AnalyticsError):
    classification = "unavailable"


class AnalyticsCooldownError(AnalyticsError):
    classification = "cooldown"


@dataclass(frozen=True, slots=True)
class MetricObservation:
    metric_key: str
    value: Decimal | int | str
    observed_at: datetime
    period_start: datetime | None = None
    period_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class AnalyticsResult:
    observations: tuple[MetricObservation, ...]
    unavailable_metric_keys: tuple[str, ...] = ()


class AnalyticsAdapter(Protocol):
    key: str
    adapter_name: str
    adapter_version: str
    source_name: str
    configured: bool

    def collect(
        self,
        receipt: PublicationReceipt,
        definitions: tuple[MetricDefinition, ...],
        *,
        observed_at: datetime,
        collection_index: int,
    ) -> AnalyticsResult: ...
