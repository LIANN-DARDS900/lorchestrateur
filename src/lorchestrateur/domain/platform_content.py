"""Durable, typed platform-adaptation artifacts and quality governance."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from lorchestrateur.domain.content import GenerationMetadata
from lorchestrateur.domain.validation import ValidationIssue


def _require_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    return value.strip()


def _require_aware_datetime(name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")


class PlatformPayload(Protocol):
    """Typed payload contract implemented by each platform module."""

    @property
    def schema_version(self) -> str: ...

    @property
    def format(self) -> str: ...

    @property
    def source_ids(self) -> tuple[str, ...]: ...

    def to_mapping(self) -> Mapping[str, Any]: ...


class PlatformValidationStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class QualityBreakdown:
    structure: int
    completeness: int
    platform_fit: int
    evidence_integrity: int
    content_hygiene: int

    def __post_init__(self) -> None:
        for field_name in (
            "structure",
            "completeness",
            "platform_fit",
            "evidence_integrity",
            "content_hygiene",
        ):
            value = getattr(self, field_name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{field_name} quality score must be an integer")
            if not 0 <= value <= 20:
                raise ValueError(f"{field_name} quality score must be between 0 and 20")

    @property
    def total(self) -> int:
        return (
            self.structure
            + self.completeness
            + self.platform_fit
            + self.evidence_integrity
            + self.content_hygiene
        )

    def to_mapping(self) -> Mapping[str, int]:
        return {
            "structure": self.structure,
            "completeness": self.completeness,
            "platform_fit": self.platform_fit,
            "evidence_integrity": self.evidence_integrity,
            "content_hygiene": self.content_hygiene,
        }


@dataclass(frozen=True, slots=True)
class QualityPolicy:
    """Configurable approval gate; the score is not an engagement prediction."""

    minimum_score: int = 80

    def __post_init__(self) -> None:
        if not isinstance(self.minimum_score, int) or isinstance(self.minimum_score, bool):
            raise ValueError("minimum quality score must be an integer")
        if not 0 <= self.minimum_score <= 100:
            raise ValueError("minimum quality score must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class PlatformContentRecord:
    """One immutable revision of content adapted from a canonical MasterContent."""

    id: str
    job_id: str
    master_content_id: str
    platform: str
    format: str
    schema_version: str
    payload: PlatformPayload
    generation_metadata: GenerationMetadata
    generation_attempt_id: str
    validation_status: PlatformValidationStatus
    quality_score: int | None
    quality_breakdown: QualityBreakdown | None
    validation_issues: tuple[ValidationIssue, ...]
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for field_name in (
            "id",
            "job_id",
            "master_content_id",
            "generation_attempt_id",
        ):
            object.__setattr__(
                self, field_name, _require_text(field_name, getattr(self, field_name))
            )
        object.__setattr__(self, "platform", _require_text("platform", self.platform).lower())
        object.__setattr__(self, "format", _require_text("format", self.format).lower())
        object.__setattr__(
            self,
            "schema_version",
            _require_text("schema_version", self.schema_version).lower(),
        )
        if not isinstance(self.validation_status, PlatformValidationStatus):
            raise ValueError("validation_status must be a PlatformValidationStatus")
        if not isinstance(self.generation_metadata, GenerationMetadata):
            raise ValueError("generation_metadata must be GenerationMetadata")
        if not isinstance(self.revision, int) or isinstance(self.revision, bool):
            raise ValueError("revision must be an integer")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        if self.payload.schema_version != self.schema_version:
            raise ValueError("payload schema version does not match the durable record")
        if self.payload.format != self.format:
            raise ValueError("payload format does not match the durable record")
        normalized_issues = tuple(self.validation_issues)
        if not all(isinstance(issue, ValidationIssue) for issue in normalized_issues):
            raise ValueError("validation_issues must contain ValidationIssue values")
        object.__setattr__(self, "validation_issues", normalized_issues)
        if self.quality_breakdown is None:
            if self.quality_score is not None:
                raise ValueError("quality_score requires a quality_breakdown")
        else:
            if not isinstance(self.quality_score, int) or isinstance(
                self.quality_score, bool
            ):
                raise ValueError("quality_score must be an integer")
            if self.quality_score != self.quality_breakdown.total:
                raise ValueError("quality_score must equal the quality breakdown total")
        if (
            self.validation_status is not PlatformValidationStatus.PENDING
            and self.quality_breakdown is None
        ):
            raise ValueError("evaluated platform content requires a quality breakdown")
        _require_aware_datetime("created_at", self.created_at)
        _require_aware_datetime("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")

    def is_approval_ready(self, policy: QualityPolicy) -> bool:
        return (
            self.validation_status is PlatformValidationStatus.PASSED
            and self.quality_score is not None
            and self.quality_score >= policy.minimum_score
        )
