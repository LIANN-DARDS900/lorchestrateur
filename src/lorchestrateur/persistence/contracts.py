"""Persistence interface consumed by application services."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from lorchestrateur.domain.content import ContentStrategy, MasterContent, SourceEvidence
from lorchestrateur.domain.platform_content import PlatformContentRecord
from lorchestrateur.domain.publication import (
    MediaAsset,
    PublicationAttempt,
    PublicationReceipt,
    PublicationRequest,
)
from lorchestrateur.domain.workflow import ContentJob, JobStep


class JobNotFoundError(LookupError):
    pass


class DuplicateJobError(ValueError):
    pass


class ConcurrentUpdateError(RuntimeError):
    pass


class ArtifactNotFoundError(LookupError):
    pass


class DuplicateArtifactError(ValueError):
    pass


class ContentJobRepository(Protocol):
    def add(self, job: ContentJob) -> None: ...

    def get(self, job_id: str) -> ContentJob: ...

    def list_jobs(self) -> tuple[ContentJob, ...]: ...

    def save(self, job: ContentJob, step: JobStep) -> None: ...

    def list_steps(self, job_id: str) -> tuple[JobStep, ...]: ...


class ContentIntelligenceRepository(ContentJobRepository, Protocol):
    def add_source_with_checkpoint(
        self, source: SourceEvidence, job: ContentJob, step: JobStep
    ) -> None: ...

    def get_source(self, source_id: str) -> SourceEvidence: ...

    def list_sources(self, job_id: str) -> tuple[SourceEvidence, ...]: ...

    def save_strategy_with_checkpoint(
        self, strategy: ContentStrategy, job: ContentJob, step: JobStep
    ) -> None: ...

    def get_strategy(self, job_id: str) -> ContentStrategy: ...

    def save_master_content_with_checkpoint(
        self, master_content: MasterContent, job: ContentJob, step: JobStep
    ) -> None: ...

    def get_master_content(self, job_id: str) -> MasterContent: ...

    def save_platform_content_with_checkpoint(
        self, content: PlatformContentRecord, job: ContentJob, step: JobStep
    ) -> None: ...

    def get_platform_content(self, content_id: str) -> PlatformContentRecord: ...

    def list_platform_contents(
        self, job_id: str, *, platform: str | None = None
    ) -> tuple[PlatformContentRecord, ...]: ...

    def get_platform_content_by_attempt(
        self,
        job_id: str,
        master_content_id: str,
        platform: str,
        generation_attempt_id: str,
    ) -> PlatformContentRecord | None: ...

    def save_platform_evaluations_with_checkpoint(
        self,
        contents: tuple[PlatformContentRecord, ...],
        job: ContentJob,
        step: JobStep,
    ) -> None: ...


class PublicationRepository(ContentIntelligenceRepository, Protocol):
    def add_publication(self, publication: PublicationRequest) -> PublicationRequest: ...

    def get_publication(self, publication_id: str) -> PublicationRequest: ...

    def get_publication_by_idempotency_key(
        self, idempotency_key: str
    ) -> PublicationRequest | None: ...

    def list_publications(self, job_id: str | None = None) -> tuple[PublicationRequest, ...]: ...

    def save_publication(self, publication: PublicationRequest) -> None: ...

    def add_publication_attempt(self, attempt: PublicationAttempt) -> None: ...

    def list_publication_attempts(self, publication_id: str) -> tuple[PublicationAttempt, ...]: ...

    def add_publication_receipt(self, receipt: PublicationReceipt) -> None: ...

    def list_publication_receipts(self, publication_id: str) -> tuple[PublicationReceipt, ...]: ...

    def add_media_asset(self, asset: MediaAsset) -> None: ...

    def list_media_assets(self, platform_content_id: str) -> tuple[MediaAsset, ...]: ...

    def claim_due_publications(
        self,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[PublicationRequest, ...]: ...

    def claim_publication(
        self,
        publication_id: str,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> PublicationRequest | None: ...

    def recover_expired_publications(self, *, now: datetime) -> tuple[PublicationRequest, ...]: ...
