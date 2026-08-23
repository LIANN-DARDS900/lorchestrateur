"""Thread-safe in-memory repository for unit tests and local composition."""

from __future__ import annotations

from threading import RLock

from lorchestrateur.domain.content import ContentStrategy, MasterContent, SourceEvidence
from lorchestrateur.domain.platform_content import PlatformContentRecord
from lorchestrateur.domain.workflow import ContentJob, JobStep
from lorchestrateur.persistence.contracts import (
    ArtifactNotFoundError,
    ConcurrentUpdateError,
    DuplicateArtifactError,
    DuplicateJobError,
    JobNotFoundError,
)


class InMemoryContentJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, ContentJob] = {}
        self._steps: dict[str, list[JobStep]] = {}
        self._sources: dict[str, SourceEvidence] = {}
        self._strategies: dict[str, ContentStrategy] = {}
        self._master_contents: dict[str, MasterContent] = {}
        self._platform_contents: dict[str, PlatformContentRecord] = {}
        self._lock = RLock()

    def add(self, job: ContentJob) -> None:
        with self._lock:
            if job.id in self._jobs:
                raise DuplicateJobError(f"content job already exists: {job.id}")
            self._jobs[job.id] = job
            self._steps[job.id] = []

    def get(self, job_id: str) -> ContentJob:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise JobNotFoundError(f"content job not found: {job_id}") from exc

    def list_jobs(self) -> tuple[ContentJob, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._jobs.values(),
                    key=lambda job: (job.created_at, job.id),
                    reverse=True,
                )
            )

    def save(self, job: ContentJob, step: JobStep) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            self._commit_checkpoint(job, step)

    def list_steps(self, job_id: str) -> tuple[JobStep, ...]:
        with self._lock:
            if job_id not in self._jobs:
                raise JobNotFoundError(f"content job not found: {job_id}")
            return tuple(self._steps[job_id])

    def add_source_with_checkpoint(
        self, source: SourceEvidence, job: ContentJob, step: JobStep
    ) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            if source.job_id != job.id:
                raise ValueError("source and checkpoint belong to different jobs")
            if source.id in self._sources:
                raise DuplicateArtifactError(f"source already exists: {source.id}")
            self._sources[source.id] = source
            self._commit_checkpoint(job, step)

    def get_source(self, source_id: str) -> SourceEvidence:
        with self._lock:
            try:
                return self._sources[source_id]
            except KeyError as exc:
                raise ArtifactNotFoundError(f"source not found: {source_id}") from exc

    def list_sources(self, job_id: str) -> tuple[SourceEvidence, ...]:
        with self._lock:
            self.get(job_id)
            return tuple(
                source for source in self._sources.values() if source.job_id == job_id
            )

    def save_strategy_with_checkpoint(
        self, strategy: ContentStrategy, job: ContentJob, step: JobStep
    ) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            if strategy.job_id != job.id:
                raise ValueError("strategy and checkpoint belong to different jobs")
            if strategy.job_id in self._strategies:
                raise DuplicateArtifactError(
                    f"content strategy already exists for job: {strategy.job_id}"
                )
            self._strategies[strategy.job_id] = strategy
            self._commit_checkpoint(job, step)

    def get_strategy(self, job_id: str) -> ContentStrategy:
        with self._lock:
            try:
                return self._strategies[job_id]
            except KeyError as exc:
                raise ArtifactNotFoundError(
                    f"content strategy not found for job: {job_id}"
                ) from exc

    def save_master_content_with_checkpoint(
        self, master_content: MasterContent, job: ContentJob, step: JobStep
    ) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            if master_content.job_id != job.id:
                raise ValueError("master content and checkpoint belong to different jobs")
            if master_content.job_id in self._master_contents:
                raise DuplicateArtifactError(
                    f"master content already exists for job: {master_content.job_id}"
                )
            self._master_contents[master_content.job_id] = master_content
            self._commit_checkpoint(job, step)

    def get_master_content(self, job_id: str) -> MasterContent:
        with self._lock:
            try:
                return self._master_contents[job_id]
            except KeyError as exc:
                raise ArtifactNotFoundError(
                    f"master content not found for job: {job_id}"
                ) from exc

    def save_platform_content_with_checkpoint(
        self, content: PlatformContentRecord, job: ContentJob, step: JobStep
    ) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            if content.job_id != job.id:
                raise ValueError("platform content and checkpoint belong to different jobs")
            if content.id in self._platform_contents:
                raise DuplicateArtifactError(
                    f"platform content already exists: {content.id}"
                )
            for existing in self._platform_contents.values():
                same_lineage = (
                    existing.job_id == content.job_id
                    and existing.master_content_id == content.master_content_id
                    and existing.platform == content.platform
                )
                if same_lineage and existing.revision == content.revision:
                    raise DuplicateArtifactError(
                        "platform content revision already exists for this job and platform"
                    )
                if (
                    same_lineage
                    and existing.generation_attempt_id == content.generation_attempt_id
                ):
                    raise DuplicateArtifactError(
                        "platform content already exists for this generation attempt"
                    )
            self._platform_contents[content.id] = content
            self._commit_checkpoint(job, step)

    def get_platform_content(self, content_id: str) -> PlatformContentRecord:
        with self._lock:
            try:
                return self._platform_contents[content_id]
            except KeyError as exc:
                raise ArtifactNotFoundError(
                    f"platform content not found: {content_id}"
                ) from exc

    def list_platform_contents(
        self, job_id: str, *, platform: str | None = None
    ) -> tuple[PlatformContentRecord, ...]:
        with self._lock:
            self.get(job_id)
            normalized_platform = platform.strip().lower() if platform else None
            return tuple(
                sorted(
                    (
                        content
                        for content in self._platform_contents.values()
                        if content.job_id == job_id
                        and (
                            normalized_platform is None
                            or content.platform == normalized_platform
                        )
                    ),
                    key=lambda content: (content.platform, content.revision, content.id),
                )
            )

    def get_platform_content_by_attempt(
        self,
        job_id: str,
        master_content_id: str,
        platform: str,
        generation_attempt_id: str,
    ) -> PlatformContentRecord | None:
        normalized_platform = platform.strip().lower()
        with self._lock:
            for content in self._platform_contents.values():
                if (
                    content.job_id == job_id
                    and content.master_content_id == master_content_id
                    and content.platform == normalized_platform
                    and content.generation_attempt_id == generation_attempt_id
                ):
                    return content
        return None

    def save_platform_evaluations_with_checkpoint(
        self,
        contents: tuple[PlatformContentRecord, ...],
        job: ContentJob,
        step: JobStep,
    ) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            for content in contents:
                existing = self._platform_contents.get(content.id)
                if existing is None:
                    raise ArtifactNotFoundError(
                        f"platform content not found: {content.id}"
                    )
                immutable_identity = (
                    existing.job_id,
                    existing.master_content_id,
                    existing.platform,
                    existing.revision,
                    existing.generation_attempt_id,
                )
                next_identity = (
                    content.job_id,
                    content.master_content_id,
                    content.platform,
                    content.revision,
                    content.generation_attempt_id,
                )
                if immutable_identity != next_identity:
                    raise ValueError("platform evaluation cannot change artifact identity")
            for content in contents:
                self._platform_contents[content.id] = content
            self._commit_checkpoint(job, step)

    def _validate_checkpoint(self, job: ContentJob, step: JobStep) -> None:
        current = self.get(job.id)
        if current.version != job.version - 1:
            raise ConcurrentUpdateError(
                f"stale content job version: expected {current.version + 1}, got {job.version}"
            )
        if step.job_id != job.id or step.sequence != job.version:
            raise ValueError("job step does not match the content job checkpoint")

    def _commit_checkpoint(self, job: ContentJob, step: JobStep) -> None:
        self._jobs[job.id] = job
        self._steps[job.id].append(step)
