"""Typed analytics definitions, historical snapshots, and collection runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Any


def _text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _aware(name: str, value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must include timezone information")


def _value(value: Decimal | int | str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("metric value must be numeric")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("metric value must be numeric") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("metric value must be finite and non-negative")
    return result


class MetricFamily(StrEnum):
    EXPOSURE = "exposure"
    INTERACTION = "interaction"
    CONVERSATION = "conversation"
    AMPLIFICATION = "amplification"
    TRAFFIC = "traffic"


class MetricUnit(StrEnum):
    COUNT = "count"
    RATIO = "ratio"
    PERCENT = "percent"


class AggregationBehavior(StrEnum):
    CUMULATIVE = "cumulative"
    INTERVAL = "interval"
    POINT_IN_TIME = "point_in_time"
    RATE = "rate"


class AnalyticsRunOutcome(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    key: str
    platform: str
    label: str
    description: str
    unit: MetricUnit
    family: MetricFamily
    aggregation: AggregationBehavior
    source: str
    version: str

    def __post_init__(self) -> None:
        for name in ("key", "platform", "label", "description", "source", "version"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        object.__setattr__(self, "platform", self.platform.lower())
        if not self.key.startswith(f"{self.platform}."):
            raise ValueError("metric key must use the platform namespace")
        if not isinstance(self.unit, MetricUnit):
            raise ValueError("unit must be a MetricUnit")
        if not isinstance(self.family, MetricFamily):
            raise ValueError("family must be a MetricFamily")
        if not isinstance(self.aggregation, AggregationBehavior):
            raise ValueError("aggregation must be an AggregationBehavior")


@dataclass(frozen=True, slots=True)
class MetricSnapshot:
    id: str
    collection_run_id: str
    publication_receipt_id: str
    job_id: str
    platform_content_id: str
    platform: str
    metric_key: str
    value: Decimal
    observed_at: datetime
    period_start: datetime | None
    period_end: datetime | None
    source: str
    source_version: str
    collected_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name in (
            "id",
            "collection_run_id",
            "publication_receipt_id",
            "job_id",
            "platform_content_id",
            "platform",
            "metric_key",
            "source",
            "source_version",
        ):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        object.__setattr__(self, "platform", self.platform.lower())
        if not self.metric_key.startswith(f"{self.platform}."):
            raise ValueError("snapshot metric does not match platform")
        object.__setattr__(self, "value", _value(self.value))
        for name in ("observed_at", "period_start", "period_end", "collected_at"):
            _aware(name, getattr(self, name))
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("metric period end cannot precede its start")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("snapshot metadata must be a mapping")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class AnalyticsCollectionRun:
    id: str
    idempotency_key: str
    platform: str
    publication_receipt_id: str
    job_id: str
    started_at: datetime
    completed_at: datetime | None
    outcome: AnalyticsRunOutcome
    adapter_name: str
    adapter_version: str
    error_classification: str | None
    metrics_collected_count: int
    unavailable_metric_keys: tuple[str, ...] = ()
    retry_count: int = 0

    def __post_init__(self) -> None:
        for name in (
            "id",
            "idempotency_key",
            "platform",
            "publication_receipt_id",
            "job_id",
            "adapter_name",
            "adapter_version",
        ):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        object.__setattr__(self, "platform", self.platform.lower())
        _aware("started_at", self.started_at)
        _aware("completed_at", self.completed_at)
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("collection completion cannot precede its start")
        if not isinstance(self.outcome, AnalyticsRunOutcome):
            raise ValueError("outcome must be an AnalyticsRunOutcome")
        if self.metrics_collected_count < 0 or self.retry_count < 0:
            raise ValueError("collection counters cannot be negative")
        if self.error_classification is not None:
            object.__setattr__(
                self,
                "error_classification",
                _text("error_classification", self.error_classification),
            )
        object.__setattr__(
            self,
            "unavailable_metric_keys",
            tuple(
                dict.fromkeys(_text("metric key", item) for item in self.unavailable_metric_keys)
            ),
        )

    def complete(
        self,
        outcome: AnalyticsRunOutcome,
        *,
        now: datetime,
        metrics_collected_count: int,
        unavailable_metric_keys: tuple[str, ...] = (),
        error_classification: str | None = None,
        retry_count: int = 0,
    ) -> AnalyticsCollectionRun:
        if self.outcome is not AnalyticsRunOutcome.RUNNING:
            raise ValueError("only a running collection can be completed")
        if outcome is AnalyticsRunOutcome.RUNNING:
            raise ValueError("completed collection requires a terminal outcome")
        return replace(
            self,
            completed_at=now,
            outcome=outcome,
            metrics_collected_count=metrics_collected_count,
            unavailable_metric_keys=unavailable_metric_keys,
            error_classification=error_classification,
            retry_count=retry_count,
        )
