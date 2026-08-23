"""Durable content-intelligence artifacts owned by a content job."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any


def _require_text(name: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} cannot be empty")
    return normalized


def _require_aware_datetime(name: str, value: datetime) -> None:
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")


def _normalize_unique_ids(name: str, values: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(_require_text(name, value) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{name} cannot contain duplicate IDs")
    return normalized


class SourceType(StrEnum):
    MANUAL = "manual"
    WEB = "web"
    DOCUMENT = "document"
    INTERVIEW = "interview"
    DATASET = "dataset"
    OTHER = "other"


class EvidenceStatus(StrEnum):
    """Review status; reviewed means eligible for use, not universally proven true."""

    UNVERIFIED = "unverified"
    REVIEWED = "reviewed"


@dataclass(frozen=True, slots=True)
class GenerationMetadata:
    provider: str
    model: str
    task: str
    generated_at: datetime
    duration_ms: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _require_text("provider", self.provider))
        object.__setattr__(self, "model", _require_text("model", self.model))
        object.__setattr__(self, "task", _require_text("task", self.task))
        _require_aware_datetime("generated_at", self.generated_at)
        if not isinstance(self.duration_ms, int) or isinstance(self.duration_ms, bool):
            raise ValueError("duration_ms must be an integer")
        if self.duration_ms < 0:
            raise ValueError("duration_ms cannot be negative")


@dataclass(frozen=True, slots=True)
class SourceEvidence:
    id: str
    job_id: str
    title: str
    url: str | None
    source_type: SourceType
    relevant_excerpt: str
    retrieved_at: datetime
    evidence_status: EvidenceStatus = EvidenceStatus.UNVERIFIED
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_text("source id", self.id))
        object.__setattr__(self, "job_id", _require_text("job_id", self.job_id))
        object.__setattr__(self, "title", _require_text("source title", self.title))
        object.__setattr__(
            self,
            "relevant_excerpt",
            _require_text("relevant_excerpt", self.relevant_excerpt),
        )
        if self.url is not None:
            if not isinstance(self.url, str):
                raise ValueError("url must be a string when provided")
            normalized_url = self.url.strip()
            object.__setattr__(self, "url", normalized_url or None)
        if not isinstance(self.source_type, SourceType):
            raise ValueError("source_type must be a SourceType")
        if not isinstance(self.evidence_status, EvidenceStatus):
            raise ValueError("evidence_status must be an EvidenceStatus")
        _require_aware_datetime("retrieved_at", self.retrieved_at)
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be a mapping")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class StrategyKeyMessage:
    message: str
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "message", _require_text("key message", self.message))
        normalized_ids = _normalize_unique_ids("key message source_ids", self.source_ids)
        if not normalized_ids:
            raise ValueError("each key message must reference at least one source")
        object.__setattr__(self, "source_ids", normalized_ids)


@dataclass(frozen=True, slots=True)
class ContentStrategy:
    id: str
    job_id: str
    objective: str
    target_audience: str
    angle: str
    tone: str
    key_messages: tuple[StrategyKeyMessage, ...]
    intended_outcome: str
    created_at: datetime
    updated_at: datetime
    generation_metadata: GenerationMetadata | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_text("strategy id", self.id))
        object.__setattr__(self, "job_id", _require_text("job_id", self.job_id))
        for field_name in (
            "objective",
            "target_audience",
            "angle",
            "tone",
            "intended_outcome",
        ):
            object.__setattr__(
                self, field_name, _require_text(field_name, getattr(self, field_name))
            )
        normalized_messages = tuple(self.key_messages)
        if not normalized_messages:
            raise ValueError("content strategy requires at least one key message")
        if not all(
            isinstance(item, StrategyKeyMessage) for item in normalized_messages
        ):
            raise ValueError("key_messages must contain StrategyKeyMessage values")
        object.__setattr__(self, "key_messages", normalized_messages)
        messages = tuple(item.message for item in normalized_messages)
        if len(messages) != len(set(messages)):
            raise ValueError("content strategy cannot contain duplicate key messages")
        _require_aware_datetime("created_at", self.created_at)
        _require_aware_datetime("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")

    @property
    def supporting_source_ids(self) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                source_id
                for key_message in self.key_messages
                for source_id in key_message.source_ids
            )
        )


@dataclass(frozen=True, slots=True)
class MasterContent:
    id: str
    job_id: str
    title: str
    summary: str
    body: str
    key_points: tuple[str, ...]
    source_ids: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    generation_metadata: GenerationMetadata | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_text("master content id", self.id))
        object.__setattr__(self, "job_id", _require_text("job_id", self.job_id))
        for field_name in ("title", "summary", "body"):
            object.__setattr__(
                self, field_name, _require_text(field_name, getattr(self, field_name))
            )

        normalized_points = tuple(
            _require_text("key point", key_point) for key_point in self.key_points
        )
        if not normalized_points:
            raise ValueError("master content requires at least one key point")
        if len(normalized_points) != len(set(normalized_points)):
            raise ValueError("master content cannot contain duplicate key points")
        object.__setattr__(self, "key_points", normalized_points)

        normalized_ids = _normalize_unique_ids("master source_ids", self.source_ids)
        if not normalized_ids:
            raise ValueError("master content must reference at least one source")
        object.__setattr__(self, "source_ids", normalized_ids)
        _require_aware_datetime("created_at", self.created_at)
        _require_aware_datetime("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
