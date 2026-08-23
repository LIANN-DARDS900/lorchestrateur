"""Thread-safe in-memory repository for unit tests and local composition."""

from __future__ import annotations

from threading import RLock

from lorchestrateur.domain.workflow import ContentJob, JobStep
from lorchestrateur.persistence.contracts import (
    ConcurrentUpdateError,
    DuplicateJobError,
    JobNotFoundError,
)


class InMemoryContentJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, ContentJob] = {}
        self._steps: dict[str, list[JobStep]] = {}
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

    def save(self, job: ContentJob, step: JobStep) -> None:
        with self._lock:
            current = self.get(job.id)
            if current.version != job.version - 1:
                raise ConcurrentUpdateError(
                    f"stale content job version: expected {current.version + 1}, got {job.version}"
                )
            if step.job_id != job.id or step.sequence != job.version:
                raise ValueError("job step does not match the content job checkpoint")
            self._jobs[job.id] = job
            self._steps[job.id].append(step)

    def list_steps(self, job_id: str) -> tuple[JobStep, ...]:
        with self._lock:
            if job_id not in self._jobs:
                raise JobNotFoundError(f"content job not found: {job_id}")
            return tuple(self._steps[job_id])

