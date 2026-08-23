"""Stable publishing contracts, errors, and adapter result types."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol

from lorchestrateur.domain.platform_content import PlatformContentRecord
from lorchestrateur.domain.publication import MediaAsset, PublicationReceipt, PublicationRequest


class PublicationError(RuntimeError):
    classification = "publication_error"
    retryable = False
    ambiguous = False


class PublicationAuthenticationError(PublicationError):
    classification = "authentication"


class PublicationPermissionError(PublicationError):
    classification = "permission"


class PublicationRateLimitError(PublicationError):
    classification = "rate_limit"
    retryable = True

    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class PublicationValidationError(PublicationError):
    classification = "validation"


class PublicationTransientError(PublicationError):
    classification = "transient"
    retryable = True


class PublicationPermanentError(PublicationError):
    classification = "permanent"


class PublicationAmbiguousOutcomeError(PublicationError):
    classification = "ambiguous_outcome"
    ambiguous = True


class PublicationUnavailableError(PublicationError):
    classification = "unavailable"


@dataclass(frozen=True, slots=True)
class PreparedItem:
    index: int
    kind: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.index < 1:
            raise ValueError("prepared item index must be positive")
        if not self.kind.strip():
            raise ValueError("prepared item kind cannot be empty")
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


@dataclass(frozen=True, slots=True)
class PreparedPublication:
    platform: str
    items: tuple[PreparedItem, ...]
    warnings: tuple[str, ...] = ()
    destination_label: str = ""

    def __post_init__(self) -> None:
        if not self.platform.strip() or not self.items:
            raise ValueError("prepared publication requires a platform and items")
        if tuple(item.index for item in self.items) != tuple(range(1, len(self.items) + 1)):
            raise ValueError("prepared publication items must be sequential")


@dataclass(frozen=True, slots=True)
class PublishedItem:
    remote_id: str
    remote_url: str | None = None
    status: str = "published"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.remote_id.strip():
            raise ValueError("remote_id cannot be empty")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    confirmed: bool
    remote_id: str | None = None
    remote_url: str | None = None


class Publisher(Protocol):
    key: str
    adapter_name: str
    adapter_version: str
    configured: bool
    destination_label: str

    def prepare(
        self,
        content: PlatformContentRecord,
        assets: tuple[MediaAsset, ...],
    ) -> PreparedPublication: ...

    def publish_item(
        self,
        publication: PublicationRequest,
        item: PreparedItem,
        *,
        parent_remote_id: str | None,
    ) -> PublishedItem: ...

    def reconcile(
        self,
        publication: PublicationRequest,
        receipts: tuple[PublicationReceipt, ...],
    ) -> ReconciliationResult: ...
