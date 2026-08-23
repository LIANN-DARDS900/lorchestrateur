"""Persistence interface consumed by application services."""

from __future__ import annotations

from typing import Protocol

from lorchestrateur.domain.workflow import ContentJob, JobStep


class JobNotFoundError(LookupError):
    pass


class DuplicateJobError(ValueError):
    pass


class ConcurrentUpdateError(RuntimeError):
    pass


class ContentJobRepository(Protocol):
    def add(self, job: ContentJob) -> None: ...

    def get(self, job_id: str) -> ContentJob: ...

    def save(self, job: ContentJob, step: JobStep) -> None: ...

    def list_steps(self, job_id: str) -> tuple[JobStep, ...]: ...

