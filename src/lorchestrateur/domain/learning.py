"""Typed, governed learning observations, recommendations, and profiles."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Any


def _text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _aware(name: str, value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must include timezone information")


def _decimal(name: str, value: Decimal | int | str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric")
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{name} must be finite")
    return result


def _mapping(name: str, value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return MappingProxyType(dict(value))


class LearningMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class LearningRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    INSUFFICIENT_DATA = "insufficient_data"
    FAILED = "failed"


class EvidenceStrength(StrEnum):
    INSUFFICIENT = "insufficient"
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class RecommendationKind(StrEnum):
    TEST_FORMAT = "test_format"
    PRESERVE_CURRENT_APPROACH = "preserve_current_approach"


class RecommendationStatus(StrEnum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class CohortDefinition:
    platform: str
    format: str
    topic_category: str
    objective: str
    metric_key: str
    window_hours: int

    def __post_init__(self) -> None:
        for name in ("platform", "format", "topic_category", "objective", "metric_key"):
            object.__setattr__(self, name, _text(name, getattr(self, name)).lower())
        if not self.metric_key.startswith(f"{self.platform}."):
            raise ValueError("cohort metric must match its platform")
        if self.window_hours not in {24, 72, 168}:
            raise ValueError("learning window must be 24, 72, or 168 hours")


@dataclass(frozen=True, slots=True)
class JobLearningContext:
    job_id: str
    workspace_id: str
    topic_category: str
    objective: str
    use_learning: bool
    mode: LearningMode
    explicit_constraints: Mapping[str, Any]
    applied_profile_entry_ids: tuple[str, ...]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for name in ("job_id", "workspace_id", "topic_category", "objective"):
            normalized = _text(name, getattr(self, name))
            if name in {"topic_category", "objective"}:
                normalized = normalized.lower()
            object.__setattr__(self, name, normalized)
        if not isinstance(self.mode, LearningMode):
            raise ValueError("mode must be a LearningMode")
        object.__setattr__(
            self,
            "explicit_constraints",
            _mapping("explicit_constraints", self.explicit_constraints),
        )
        object.__setattr__(
            self,
            "applied_profile_entry_ids",
            tuple(
                dict.fromkeys(
                    _text("profile entry id", item) for item in self.applied_profile_entry_ids
                )
            ),
        )
        _aware("created_at", self.created_at)
        _aware("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("learning context update cannot precede creation")

    def with_applied_entries(
        self, entry_ids: tuple[str, ...], *, now: datetime
    ) -> JobLearningContext:
        return replace(self, applied_profile_entry_ids=entry_ids, updated_at=now)


@dataclass(frozen=True, slots=True)
class LearningAnalysisRun:
    id: str
    idempotency_key: str
    workspace_id: str
    mode: LearningMode
    cohort_a: CohortDefinition
    cohort_b: CohortDefinition
    algorithm_version: str
    minimum_sample_size: int
    started_at: datetime
    completed_at: datetime | None
    status: LearningRunStatus
    sample_count_a: int
    sample_count_b: int
    failure_classification: str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "idempotency_key", "workspace_id", "algorithm_version"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        if not isinstance(self.mode, LearningMode):
            raise ValueError("mode must be a LearningMode")
        if self.cohort_a.platform != self.cohort_b.platform:
            raise ValueError("compared cohorts must use the same platform")
        if self.cohort_a.metric_key != self.cohort_b.metric_key:
            raise ValueError("compared cohorts must use the same metric")
        if self.cohort_a.window_hours != self.cohort_b.window_hours:
            raise ValueError("compared cohorts must use the same window")
        if self.cohort_a.format == self.cohort_b.format:
            raise ValueError("compared cohorts must use different formats")
        if self.minimum_sample_size < 2:
            raise ValueError("minimum sample size must be at least two")
        if self.sample_count_a < 0 or self.sample_count_b < 0:
            raise ValueError("sample counts cannot be negative")
        _aware("started_at", self.started_at)
        _aware("completed_at", self.completed_at)
        if self.completed_at is not None and self.completed_at < self.started_at:
            raise ValueError("learning run completion cannot precede its start")
        if self.failure_classification is not None:
            object.__setattr__(
                self,
                "failure_classification",
                _text("failure_classification", self.failure_classification),
            )


@dataclass(frozen=True, slots=True)
class PerformanceObservation:
    id: str
    analysis_run_id: str
    workspace_id: str
    mode: LearningMode
    platform: str
    metric_key: str
    window_hours: int
    cohort_a_format: str
    cohort_b_format: str
    sample_count_a: int
    sample_count_b: int
    median_a: Decimal
    median_b: Decimal
    mean_a: Decimal
    mean_b: Decimal
    relative_difference_percent: Decimal
    evidence_strength: EvidenceStrength
    evidence_breakdown: Mapping[str, Any]
    publication_ids: tuple[str, ...]
    receipt_ids: tuple[str, ...]
    snapshot_ids: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        for name in (
            "id",
            "analysis_run_id",
            "workspace_id",
            "platform",
            "metric_key",
            "cohort_a_format",
            "cohort_b_format",
        ):
            normalized = _text(name, getattr(self, name))
            if name in {"platform", "metric_key", "cohort_a_format", "cohort_b_format"}:
                normalized = normalized.lower()
            object.__setattr__(self, name, normalized)
        if not isinstance(self.mode, LearningMode):
            raise ValueError("mode must be a LearningMode")
        if not isinstance(self.evidence_strength, EvidenceStrength):
            raise ValueError("evidence_strength must be an EvidenceStrength")
        if self.sample_count_a < 1 or self.sample_count_b < 1:
            raise ValueError("observations require samples in both cohorts")
        for name in ("median_a", "median_b", "mean_a", "mean_b", "relative_difference_percent"):
            object.__setattr__(self, name, _decimal(name, getattr(self, name)))
        object.__setattr__(
            self, "evidence_breakdown", _mapping("evidence_breakdown", self.evidence_breakdown)
        )
        for name in ("publication_ids", "receipt_ids", "snapshot_ids"):
            object.__setattr__(
                self,
                name,
                tuple(dict.fromkeys(_text(name, item) for item in getattr(self, name))),
            )
        _aware("created_at", self.created_at)


@dataclass(frozen=True, slots=True)
class OptimizationRecommendation:
    id: str
    observation_id: str
    workspace_id: str
    mode: LearningMode
    platform: str
    topic_category: str
    objective: str
    kind: RecommendationKind
    parameters: Mapping[str, Any]
    rationale: str
    evidence_strength: EvidenceStrength
    status: RecommendationStatus
    created_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    potentially_outdated: bool = False

    def __post_init__(self) -> None:
        for name in (
            "id",
            "observation_id",
            "workspace_id",
            "platform",
            "topic_category",
            "objective",
            "rationale",
        ):
            normalized = _text(name, getattr(self, name))
            if name in {"platform", "topic_category", "objective"}:
                normalized = normalized.lower()
            object.__setattr__(self, name, normalized)
        if not isinstance(self.mode, LearningMode):
            raise ValueError("mode must be a LearningMode")
        if not isinstance(self.kind, RecommendationKind):
            raise ValueError("kind must be a RecommendationKind")
        if not isinstance(self.status, RecommendationStatus):
            raise ValueError("status must be a RecommendationStatus")
        if not isinstance(self.evidence_strength, EvidenceStrength):
            raise ValueError("evidence_strength must be an EvidenceStrength")
        object.__setattr__(self, "parameters", _mapping("parameters", self.parameters))
        _aware("created_at", self.created_at)
        _aware("expires_at", self.expires_at)
        _aware("decided_at", self.decided_at)
        if self.expires_at <= self.created_at:
            raise ValueError("recommendation expiry must follow creation")
        if self.decided_by is not None:
            object.__setattr__(self, "decided_by", _text("decided_by", self.decided_by))
        object.__setattr__(self, "decision_reason", _optional_text(self.decision_reason))
        if self.status is RecommendationStatus.PROPOSED and self.decided_at is not None:
            raise ValueError("proposed recommendation cannot already have a decision")
        if self.status is not RecommendationStatus.PROPOSED and self.decided_at is None:
            raise ValueError("terminal recommendation status requires a decision timestamp")

    def decide(
        self,
        status: RecommendationStatus,
        *,
        decided_by: str,
        now: datetime,
        reason: str | None = None,
    ) -> OptimizationRecommendation:
        if self.status is not RecommendationStatus.PROPOSED:
            raise ValueError("only a proposed recommendation can be decided")
        if status not in {RecommendationStatus.ACCEPTED, RecommendationStatus.REJECTED}:
            raise ValueError("human decisions may only accept or reject")
        return replace(
            self,
            status=status,
            decided_at=now,
            decided_by=decided_by,
            decision_reason=reason,
        )


@dataclass(frozen=True, slots=True)
class LearningProfile:
    id: str
    workspace_id: str
    mode: LearningMode
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for name in ("id", "workspace_id"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        if not isinstance(self.mode, LearningMode):
            raise ValueError("mode must be a LearningMode")
        _aware("created_at", self.created_at)
        _aware("updated_at", self.updated_at)


@dataclass(frozen=True, slots=True)
class LearningProfileEntry:
    id: str
    profile_id: str
    recommendation_id: str
    platform: str
    topic_category: str
    objective: str
    kind: RecommendationKind
    parameters: Mapping[str, Any]
    evidence_strength: EvidenceStrength
    accepted_at: datetime
    expires_at: datetime
    active: bool = True

    def __post_init__(self) -> None:
        for name in (
            "id",
            "profile_id",
            "recommendation_id",
            "platform",
            "topic_category",
            "objective",
        ):
            normalized = _text(name, getattr(self, name))
            if name in {"platform", "topic_category", "objective"}:
                normalized = normalized.lower()
            object.__setattr__(self, name, normalized)
        if not isinstance(self.kind, RecommendationKind):
            raise ValueError("kind must be a RecommendationKind")
        if not isinstance(self.evidence_strength, EvidenceStrength):
            raise ValueError("evidence_strength must be an EvidenceStrength")
        object.__setattr__(self, "parameters", _mapping("parameters", self.parameters))
        _aware("accepted_at", self.accepted_at)
        _aware("expires_at", self.expires_at)
        if self.expires_at <= self.accepted_at:
            raise ValueError("profile entry expiry must follow acceptance")


@dataclass(frozen=True, slots=True)
class LearningAuditEvent:
    id: str
    event: str
    entity_type: str
    entity_id: str
    actor: str
    metadata: Mapping[str, Any]
    created_at: datetime

    def __post_init__(self) -> None:
        for name in ("id", "event", "entity_type", "entity_id", "actor"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        object.__setattr__(self, "metadata", _mapping("metadata", self.metadata))
        _aware("created_at", self.created_at)
