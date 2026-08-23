"""Persistence interface consumed by application services."""

from __future__ import annotations

from typing import Protocol

from lorchestrateur.domain.content import ContentStrategy, MasterContent, SourceEvidence
from lorchestrateur.domain.platform_content import PlatformContentRecord
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
