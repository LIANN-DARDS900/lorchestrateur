"""Explicit orchestration use cases; no autonomous agent communication."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from lorchestrateur.ai.contracts import AIRequest, AIResponse
from lorchestrateur.ai.router import AIRouter, AIUnavailableError
from lorchestrateur.domain.validation import ValidationResult
from lorchestrateur.domain.workflow import ContentJob, ContentJobState, StateMachine
from lorchestrateur.persistence.contracts import ContentJobRepository
from lorchestrateur.platforms.contracts import PlatformContent
from lorchestrateur.platforms.registry import PlatformRegistry


class OrchestrationConfigurationError(RuntimeError):
    pass


class PlatformContentBatchError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class AIStageOutcome:
    job: ContentJob
    response: AIResponse | None
    paused: bool


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    job: ContentJob
    reports: Mapping[str, ValidationResult]
    repair_requested: bool
    paused: bool


_AI_STAGE_TRANSITIONS = {
    ContentJobState.STRATEGIZING: ContentJobState.GENERATING_MASTER,
    ContentJobState.GENERATING_MASTER: ContentJobState.ADAPTING_PLATFORMS,
    ContentJobState.ADAPTING_PLATFORMS: ContentJobState.VALIDATING,
}


class OrchestrationService:
    """Coordinates one explicit use case at a time and persists every state change."""

    def __init__(
        self,
        repository: ContentJobRepository,
        state_machine: StateMachine,
        platforms: PlatformRegistry,
        *,
        ai_router: AIRouter | None = None,
    ) -> None:
        self._repository = repository
        self._state_machine = state_machine
        self._platforms = platforms
        self._ai_router = ai_router

    def create_job(
        self,
        *,
        workspace_id: str,
        idea: str,
        target_platforms: tuple[str, ...],
        job_id: str | None = None,
    ) -> ContentJob:
        job = ContentJob.create(
            workspace_id=workspace_id,
            idea=idea,
            target_platforms=target_platforms,
            job_id=job_id,
        )
        for platform in job.target_platforms:
            self._platforms.get(platform)
        self._repository.add(job)
        return job

    def transition(
        self,
        job_id: str,
        target: ContentJobState,
        *,
        event: str,
        details: Mapping[str, object] | None = None,
    ) -> ContentJob:
        current = self._repository.get(job_id)
        updated, step = self._state_machine.transition(
            current, target, event=event, details=details
        )
        self._repository.save(updated, step)
        return updated

    def complete_ai_stage(
        self,
        job_id: str,
        request: AIRequest,
        *,
        preferred_provider: str | None = None,
    ) -> AIStageOutcome:
        current = self._repository.get(job_id)
        target = _AI_STAGE_TRANSITIONS.get(current.state)
        if target is None:
            raise ValueError(f"state {current.state} is not an AI generation stage")
        if self._ai_router is None:
            raise OrchestrationConfigurationError("AI router is not configured")

        try:
            response = self._ai_router.generate(
                request, preferred_provider=preferred_provider
            )
        except AIUnavailableError as exc:
            attempts = [
                {"provider": item.provider, "outcome": item.outcome}
                for item in exc.attempts
            ]
            paused, step = self._state_machine.pause(
                current,
                reason="AI providers unavailable under current policy",
                details={"task": request.task.value, "attempts": attempts},
            )
            self._repository.save(paused, step)
            return AIStageOutcome(job=paused, response=None, paused=True)

        updated, step = self._state_machine.transition(
            current,
            target,
            event="ai_stage_completed",
            details={
                "task": request.task.value,
                "provider": response.provider,
                "model": response.model,
                "response_characters": len(response.content),
            },
        )
        self._repository.save(updated, step)
        return AIStageOutcome(job=updated, response=response, paused=False)

    def validate_platform_content(
        self,
        job_id: str,
        content_by_platform: Mapping[str, PlatformContent],
    ) -> ValidationOutcome:
        current = self._repository.get(job_id)
        if current.state is not ContentJobState.VALIDATING:
            raise ValueError("platform content can only be validated in the validating state")

        expected = set(current.target_platforms)
        provided = set(content_by_platform)
        if provided != expected:
            missing = sorted(expected - provided)
            extra = sorted(provided - expected)
            raise PlatformContentBatchError(
                f"platform content set mismatch; missing={missing}, extra={extra}"
            )

        reports = {
            platform_key: self._platforms.get(platform_key).validate(
                content_by_platform[platform_key]
            )
            for platform_key in current.target_platforms
        }
        issue_counts = {
            platform_key: len(report.issues) for platform_key, report in reports.items()
        }

        if all(report.is_valid for report in reports.values()):
            updated, step = self._state_machine.transition(
                current,
                ContentJobState.AWAITING_APPROVAL,
                event="platform_validation_passed",
                details={"issue_counts": issue_counts},
            )
            repair_requested = False
            paused = False
        else:
            updated, step = self._state_machine.request_controlled_repair(
                current,
                details={"issue_counts": issue_counts},
            )
            repair_requested = updated.state is ContentJobState.ADAPTING_PLATFORMS
            paused = updated.state is ContentJobState.PAUSED

        self._repository.save(updated, step)
        return ValidationOutcome(
            job=updated,
            reports=reports,
            repair_requested=repair_requested,
            paused=paused,
        )

    def approve(self, job_id: str, *, approved_by: str) -> ContentJob:
        actor = approved_by.strip()
        if not actor:
            raise ValueError("approved_by cannot be empty")
        return self.transition(
            job_id,
            ContentJobState.APPROVED,
            event="human_approval_recorded",
            details={"approved_by": actor},
        )

    def pause(self, job_id: str, *, reason: str) -> ContentJob:
        current = self._repository.get(job_id)
        updated, step = self._state_machine.pause(current, reason=reason)
        self._repository.save(updated, step)
        return updated

    def resume(self, job_id: str) -> ContentJob:
        current = self._repository.get(job_id)
        updated, step = self._state_machine.resume(current)
        self._repository.save(updated, step)
        return updated

    def fail(self, job_id: str, *, reason: str) -> ContentJob:
        current = self._repository.get(job_id)
        updated, step = self._state_machine.fail(current, reason=reason)
        self._repository.save(updated, step)
        return updated

