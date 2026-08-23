"""Public orchestration use cases composed from focused application components."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from lorchestrateur.ai.contracts import AIRequest, AIResponse
from lorchestrateur.ai.router import AIRouter, AIUnavailableError
from lorchestrateur.application.content_intelligence import (
    ContentIntelligencePipeline,
    MasterContentGenerationOutcome,
    ResearchCompletionOutcome,
    SourceAdditionOutcome,
    StrategyGenerationOutcome,
)
from lorchestrateur.application.platform_adaptation import (
    PlatformAdaptationOutcome,
    PlatformAdaptationPipeline,
    PlatformEvaluationOutcome,
)
from lorchestrateur.domain.content import EvidenceStatus, SourceType
from lorchestrateur.domain.platform_content import QualityPolicy
from lorchestrateur.domain.validation import ValidationResult
from lorchestrateur.domain.workflow import (
    ContentJob,
    ContentJobState,
    StateMachine,
    utc_now,
)
from lorchestrateur.persistence.contracts import (
    ArtifactNotFoundError,
    ContentIntelligenceRepository,
)
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
        repository: ContentIntelligenceRepository,
        state_machine: StateMachine,
        platforms: PlatformRegistry,
        *,
        ai_router: AIRouter | None = None,
        quality_policy: QualityPolicy | None = None,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self._repository = repository
        self._state_machine = state_machine
        self._platforms = platforms
        self._ai_router = ai_router
        self._content_intelligence = ContentIntelligencePipeline(
            repository,
            state_machine,
            ai_router=ai_router,
            clock=clock,
            id_factory=id_factory,
            timer=timer,
        )
        self._platform_adaptation = PlatformAdaptationPipeline(
            repository,
            state_machine,
            platforms,
            ai_router=ai_router,
            quality_policy=quality_policy,
            clock=clock,
            id_factory=id_factory,
            timer=timer,
        )

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

    def begin_research(self, job_id: str) -> ContentJob:
        return self._content_intelligence.begin_research(job_id)

    def add_source(
        self,
        job_id: str,
        *,
        title: str,
        relevant_excerpt: str,
        source_type: SourceType,
        url: str | None = None,
        retrieved_at: datetime | None = None,
        evidence_status: EvidenceStatus = EvidenceStatus.UNVERIFIED,
        metadata: Mapping[str, Any] | None = None,
        source_id: str | None = None,
    ) -> SourceAdditionOutcome:
        return self._content_intelligence.add_source(
            job_id,
            title=title,
            relevant_excerpt=relevant_excerpt,
            source_type=source_type,
            url=url,
            retrieved_at=retrieved_at,
            evidence_status=evidence_status,
            metadata=metadata,
            source_id=source_id,
        )

    def complete_research(self, job_id: str) -> ResearchCompletionOutcome:
        return self._content_intelligence.complete_research(job_id)

    def generate_content_strategy(self, job_id: str) -> StrategyGenerationOutcome:
        return self._content_intelligence.generate_content_strategy(job_id)

    def generate_master_content(self, job_id: str) -> MasterContentGenerationOutcome:
        return self._content_intelligence.generate_master_content(job_id)

    def adapt_platforms(
        self, job_id: str, *, generation_attempt_id: str | None = None
    ) -> PlatformAdaptationOutcome:
        return self._platform_adaptation.adapt_platforms(
            job_id, generation_attempt_id=generation_attempt_id
        )

    def evaluate_platform_adaptations(
        self, job_id: str
    ) -> PlatformEvaluationOutcome:
        return self._platform_adaptation.evaluate_platforms(job_id)

    def validate_platform_adaptations(
        self, job_id: str
    ) -> PlatformEvaluationOutcome:
        """Named alias matching the deterministic validation stage."""

        return self.evaluate_platform_adaptations(job_id)

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
        """Phase 1 compatibility path; durable Phase 2 stages use dedicated methods."""
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
        try:
            self._repository.get_master_content(job_id)
        except ArtifactNotFoundError:
            pass
        else:
            raise PlatformContentBatchError(
                "jobs with persisted master content require durable platform adaptations"
            )

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
