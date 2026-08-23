"""Deterministic content-job state machine and trace records."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


class ContentJobState(StrEnum):
    CREATED = "created"
    RESEARCHING = "researching"
    STRATEGIZING = "strategizing"
    GENERATING_MASTER = "generating_master"
    ADAPTING_PLATFORMS = "adapting_platforms"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"
    PAUSED = "paused"


TERMINAL_STATES = frozenset({ContentJobState.PUBLISHED, ContentJobState.FAILED})

_FORWARD_TRANSITIONS: Mapping[ContentJobState, frozenset[ContentJobState]] = {
    ContentJobState.CREATED: frozenset({ContentJobState.RESEARCHING}),
    ContentJobState.RESEARCHING: frozenset({ContentJobState.STRATEGIZING}),
    ContentJobState.STRATEGIZING: frozenset({ContentJobState.GENERATING_MASTER}),
    ContentJobState.GENERATING_MASTER: frozenset({ContentJobState.ADAPTING_PLATFORMS}),
    ContentJobState.ADAPTING_PLATFORMS: frozenset({ContentJobState.VALIDATING}),
    ContentJobState.VALIDATING: frozenset({ContentJobState.AWAITING_APPROVAL}),
    ContentJobState.AWAITING_APPROVAL: frozenset({ContentJobState.APPROVED}),
    ContentJobState.APPROVED: frozenset({ContentJobState.PUBLISHING}),
    ContentJobState.PUBLISHING: frozenset({ContentJobState.PUBLISHED}),
}


class StateTransitionError(ValueError):
    """Raised when a requested workflow transition violates the state model."""


@dataclass(frozen=True, slots=True)
class ContentJob:
    id: str
    workspace_id: str
    idea: str
    target_platforms: tuple[str, ...]
    state: ContentJobState
    version: int
    repair_attempts: int
    paused_from: ContentJobState | None
    status_message: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def create(
        cls,
        *,
        workspace_id: str,
        idea: str,
        target_platforms: tuple[str, ...],
        job_id: str | None = None,
        now: datetime | None = None,
    ) -> ContentJob:
        normalized_workspace = workspace_id.strip()
        normalized_idea = idea.strip()
        normalized_platforms = tuple(
            dict.fromkeys(
                platform.strip().lower()
                for platform in target_platforms
                if platform.strip()
            )
        )
        if not normalized_workspace:
            raise ValueError("workspace_id cannot be empty")
        if not normalized_idea:
            raise ValueError("idea cannot be empty")
        if not normalized_platforms:
            raise ValueError("at least one target platform is required")

        timestamp = now or utc_now()
        return cls(
            id=job_id or str(uuid4()),
            workspace_id=normalized_workspace,
            idea=normalized_idea,
            target_platforms=normalized_platforms,
            state=ContentJobState.CREATED,
            version=0,
            repair_attempts=0,
            paused_from=None,
            status_message=None,
            created_at=timestamp,
            updated_at=timestamp,
        )


@dataclass(frozen=True, slots=True)
class JobStep:
    id: str
    job_id: str
    sequence: int
    event: str
    from_state: ContentJobState
    to_state: ContentJobState
    details: Mapping[str, Any]
    created_at: datetime


class StateMachine:
    """Applies valid transitions and emits an immutable trace entry for each mutation."""

    max_repair_attempts = 1

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._clock = clock
        self._id_factory = id_factory

    def transition(
        self,
        job: ContentJob,
        target: ContentJobState,
        *,
        event: str = "state_transition",
        details: Mapping[str, Any] | None = None,
    ) -> tuple[ContentJob, JobStep]:
        allowed = _FORWARD_TRANSITIONS.get(job.state, frozenset())
        if target not in allowed:
            raise StateTransitionError(f"cannot transition from {job.state} to {target}")
        return self._apply(job, target, event=event, details=details)

    def record_event(
        self,
        job: ContentJob,
        *,
        event: str,
        details: Mapping[str, Any] | None = None,
    ) -> tuple[ContentJob, JobStep]:
        if job.state in TERMINAL_STATES:
            raise StateTransitionError(f"cannot record work for a job in state {job.state}")
        if not event.strip():
            raise ValueError("event cannot be empty")
        return self._apply(job, job.state, event=event, details=details)

    def pause(
        self,
        job: ContentJob,
        *,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> tuple[ContentJob, JobStep]:
        if job.state in TERMINAL_STATES or job.state is ContentJobState.PAUSED:
            raise StateTransitionError(f"cannot pause a job in state {job.state}")
        merged_details = {"reason": reason, **(details or {})}
        return self._apply(
            job,
            ContentJobState.PAUSED,
            event="workflow_paused",
            details=merged_details,
            paused_from=job.state,
            status_message=reason,
        )

    def resume(self, job: ContentJob) -> tuple[ContentJob, JobStep]:
        if job.state is not ContentJobState.PAUSED or job.paused_from is None:
            raise StateTransitionError("only a paused job with a checkpoint can be resumed")
        return self._apply(
            job,
            job.paused_from,
            event="workflow_resumed",
            details={"checkpoint_state": job.paused_from.value},
            paused_from=None,
            status_message=None,
        )

    def fail(
        self,
        job: ContentJob,
        *,
        reason: str,
        details: Mapping[str, Any] | None = None,
    ) -> tuple[ContentJob, JobStep]:
        if job.state in TERMINAL_STATES:
            raise StateTransitionError(f"cannot fail a job in state {job.state}")
        merged_details = {"reason": reason, **(details or {})}
        return self._apply(
            job,
            ContentJobState.FAILED,
            event="workflow_failed",
            details=merged_details,
            paused_from=None,
            status_message=reason,
        )

    def request_controlled_repair(
        self,
        job: ContentJob,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> tuple[ContentJob, JobStep]:
        if job.state is not ContentJobState.VALIDATING:
            raise StateTransitionError("controlled repair can only start from validating")
        if job.repair_attempts >= self.max_repair_attempts:
            return self.pause(
                job,
                reason="controlled repair budget exhausted",
                details=details,
            )
        return self._apply(
            job,
            ContentJobState.ADAPTING_PLATFORMS,
            event="controlled_repair_requested",
            details={"repair_attempt": job.repair_attempts + 1, **(details or {})},
            repair_attempts=job.repair_attempts + 1,
        )

    def _apply(
        self,
        job: ContentJob,
        target: ContentJobState,
        *,
        event: str,
        details: Mapping[str, Any] | None,
        repair_attempts: int | None = None,
        paused_from: ContentJobState | None = None,
        status_message: str | None = None,
    ) -> tuple[ContentJob, JobStep]:
        timestamp = self._clock()
        next_version = job.version + 1
        next_job = replace(
            job,
            state=target,
            version=next_version,
            repair_attempts=(
                job.repair_attempts if repair_attempts is None else repair_attempts
            ),
            paused_from=paused_from,
            status_message=status_message,
            updated_at=timestamp,
        )
        step = JobStep(
            id=self._id_factory(),
            job_id=job.id,
            sequence=next_version,
            event=event,
            from_state=job.state,
            to_state=target,
            details=MappingProxyType(dict(details or {})),
            created_at=timestamp,
        )
        return next_job, step
