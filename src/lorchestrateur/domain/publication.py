"""Typed publication, media, attempt, receipt, and lease domain records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
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


class PublicationMode(StrEnum):
    PUBLISH_NOW = "publish_now"
    SCHEDULED = "scheduled"


class PublicationStatus(StrEnum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    READY = "ready"
    PUBLISHING = "publishing"
    DRY_RUN_COMPLETED = "dry_run_completed"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"
    NEEDS_RECONCILIATION = "needs_reconciliation"


class PublicationAttemptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    DRY_RUN = "dry_run"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    AMBIGUOUS = "ambiguous"


class MediaAssetType(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


_STATUS_TRANSITIONS = {
    PublicationStatus.DRAFT: frozenset(
        {PublicationStatus.READY, PublicationStatus.SCHEDULED, PublicationStatus.CANCELLED}
    ),
    PublicationStatus.SCHEDULED: frozenset({PublicationStatus.READY, PublicationStatus.CANCELLED}),
    PublicationStatus.READY: frozenset(
        {
            PublicationStatus.PUBLISHING,
            PublicationStatus.FAILED,
            PublicationStatus.CANCELLED,
        }
    ),
    PublicationStatus.PUBLISHING: frozenset(
        {
            PublicationStatus.PUBLISHED,
            PublicationStatus.DRY_RUN_COMPLETED,
            PublicationStatus.FAILED,
            PublicationStatus.NEEDS_RECONCILIATION,
            PublicationStatus.READY,
        }
    ),
    PublicationStatus.FAILED: frozenset({PublicationStatus.READY, PublicationStatus.CANCELLED}),
    PublicationStatus.NEEDS_RECONCILIATION: frozenset(
        {PublicationStatus.PUBLISHED, PublicationStatus.FAILED}
    ),
}


@dataclass(frozen=True, slots=True)
class PublicationRequest:
    id: str
    job_id: str
    platform_content_id: str
    platform: str
    requested_by: str
    mode: PublicationMode
    scheduled_at: datetime | None
    idempotency_key: str
    status: PublicationStatus
    dry_run: bool
    claim_owner: str | None
    claimed_at: datetime | None
    lease_expires_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for name in ("id", "job_id", "platform_content_id", "requested_by", "idempotency_key"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        object.__setattr__(self, "platform", _text("platform", self.platform).lower())
        if not isinstance(self.mode, PublicationMode):
            raise ValueError("mode must be a PublicationMode")
        if not isinstance(self.status, PublicationStatus):
            raise ValueError("status must be a PublicationStatus")
        if self.mode is PublicationMode.SCHEDULED and self.scheduled_at is None:
            raise ValueError("scheduled publication requires scheduled_at")
        if self.mode is PublicationMode.PUBLISH_NOW and self.scheduled_at is not None:
            raise ValueError("publish-now request cannot define scheduled_at")
        for name in ("scheduled_at", "claimed_at", "lease_expires_at", "created_at", "updated_at"):
            _aware(name, getattr(self, name))
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.claim_owner is not None:
            object.__setattr__(self, "claim_owner", _text("claim_owner", self.claim_owner))
            if self.claimed_at is None or self.lease_expires_at is None:
                raise ValueError("claimed publication requires claim timestamps")
            if self.lease_expires_at <= self.claimed_at:
                raise ValueError("publication lease must expire after it is claimed")
        elif self.claimed_at is not None or self.lease_expires_at is not None:
            raise ValueError("claim timestamps require claim_owner")

    def transition(
        self,
        status: PublicationStatus,
        *,
        now: datetime,
        clear_claim: bool = False,
    ) -> PublicationRequest:
        allowed = _STATUS_TRANSITIONS.get(self.status, frozenset())
        if status not in allowed:
            raise ValueError(f"cannot transition publication from {self.status} to {status}")
        _aware("now", now)
        return replace(
            self,
            status=status,
            claim_owner=None if clear_claim else self.claim_owner,
            claimed_at=None if clear_claim else self.claimed_at,
            lease_expires_at=None if clear_claim else self.lease_expires_at,
            updated_at=now,
        )


@dataclass(frozen=True, slots=True)
class PublicationAttempt:
    id: str
    publication_id: str
    attempt_number: int
    adapter_name: str
    started_at: datetime
    finished_at: datetime
    outcome: PublicationAttemptOutcome
    error_classification: str | None = None
    remote_identifier: str | None = None

    def __post_init__(self) -> None:
        for name in ("id", "publication_id", "adapter_name"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        if self.attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        _aware("started_at", self.started_at)
        _aware("finished_at", self.finished_at)
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot be earlier than started_at")
        if not isinstance(self.outcome, PublicationAttemptOutcome):
            raise ValueError("outcome must be a PublicationAttemptOutcome")
        if self.error_classification is not None:
            object.__setattr__(
                self,
                "error_classification",
                _text("error_classification", self.error_classification),
            )


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    id: str
    publication_id: str
    platform: str
    item_index: int
    remote_id: str
    remote_url: str | None
    published_at: datetime
    adapter_name: str
    adapter_version: str
    status: str
    delivery_kind: str
    metadata: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        for name in (
            "id",
            "publication_id",
            "platform",
            "remote_id",
            "adapter_name",
            "adapter_version",
            "status",
            "delivery_kind",
        ):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        if self.item_index < 1:
            raise ValueError("item_index must be positive")
        _aware("published_at", self.published_at)
        if not isinstance(self.metadata, Mapping):
            raise ValueError("receipt metadata must be a mapping")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class MediaAsset:
    id: str
    job_id: str
    platform_content_id: str
    media_type: MediaAssetType
    source_url: str
    order: int
    alt_text: str | None
    created_at: datetime

    def __post_init__(self) -> None:
        for name in ("id", "job_id", "platform_content_id", "source_url"):
            object.__setattr__(self, name, _text(name, getattr(self, name)))
        if not isinstance(self.media_type, MediaAssetType):
            raise ValueError("media_type must be a MediaAssetType")
        if self.order < 1:
            raise ValueError("media order must be positive")
        if self.alt_text is not None:
            normalized = self.alt_text.strip()
            object.__setattr__(self, "alt_text", normalized or None)
        _aware("created_at", self.created_at)
