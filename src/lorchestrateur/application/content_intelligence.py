"""Evidence-aware strategy and canonical master-content pipeline."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Any
from uuid import uuid4

from lorchestrateur.ai.contracts import (
    AIOutputSchema,
    AIRequest,
    AIResponse,
    AITask,
)
from lorchestrateur.ai.router import AIRouter, AIUnavailableError
from lorchestrateur.ai.structured import (
    ContentStrategyOutput,
    MasterContentOutput,
    StructuredOutputError,
)
from lorchestrateur.domain.content import (
    ContentStrategy,
    EvidenceStatus,
    GenerationMetadata,
    MasterContent,
    SourceEvidence,
    SourceType,
    StrategyKeyMessage,
)
from lorchestrateur.domain.content_validation import (
    ContentValidationError,
    validate_master_content,
    validate_research_sources,
    validate_source,
    validate_strategy,
)
from lorchestrateur.domain.validation import ValidationIssue, ValidationResult
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


class ContentIntelligenceConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SourceAdditionOutcome:
    job: ContentJob
    source: SourceEvidence


@dataclass(frozen=True, slots=True)
class ResearchCompletionOutcome:
    job: ContentJob
    validation: ValidationResult
    paused: bool


@dataclass(frozen=True, slots=True)
class StrategyGenerationOutcome:
    job: ContentJob
    strategy: ContentStrategy | None
    validation: ValidationResult
    paused: bool


@dataclass(frozen=True, slots=True)
class MasterContentGenerationOutcome:
    job: ContentJob
    master_content: MasterContent | None
    validation: ValidationResult
    paused: bool


class ContentIntelligencePipeline:
    """Runs explicit Phase 2 stages without choosing policy or workflow states via AI."""

    def __init__(
        self,
        repository: ContentIntelligenceRepository,
        state_machine: StateMachine,
        *,
        ai_router: AIRouter | None,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self._repository = repository
        self._state_machine = state_machine
        self._ai_router = ai_router
        self._clock = clock
        self._id_factory = id_factory
        self._timer = timer

    def begin_research(self, job_id: str) -> ContentJob:
        current = self._repository.get(job_id)
        updated, step = self._state_machine.transition(
            current,
            ContentJobState.RESEARCHING,
            event="research_started",
        )
        self._repository.save(updated, step)
        return updated

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
        current = self._repository.get(job_id)
        self._require_state(current, ContentJobState.RESEARCHING, "add source evidence")
        source = SourceEvidence(
            id=source_id or self._id_factory(),
            job_id=current.id,
            title=title,
            url=url,
            source_type=source_type,
            relevant_excerpt=relevant_excerpt,
            retrieved_at=retrieved_at or self._clock(),
            evidence_status=evidence_status,
            metadata=metadata or {},
        )
        validation = validate_source(source)
        if not validation.is_valid:
            raise ContentValidationError(validation)

        updated, step = self._state_machine.record_event(
            current,
            event="source_evidence_added",
            details={
                "source_id": source.id,
                "source_type": source.source_type.value,
                "evidence_status": source.evidence_status.value,
                "has_url": source.url is not None,
            },
        )
        self._repository.add_source_with_checkpoint(source, updated, step)
        return SourceAdditionOutcome(job=updated, source=source)

    def complete_research(self, job_id: str) -> ResearchCompletionOutcome:
        current = self._repository.get(job_id)
        self._require_state(current, ContentJobState.RESEARCHING, "complete research")
        sources = self._repository.list_sources(job_id)
        validation = validate_research_sources(sources)
        if not validation.is_valid:
            paused = self._pause_for_controlled_intervention(
                current,
                reason="research evidence is not ready",
                stage="research",
                details={"issue_codes": self._issue_codes(validation)},
            )
            return ResearchCompletionOutcome(
                job=paused, validation=validation, paused=True
            )

        reviewed_count = sum(
            source.evidence_status is EvidenceStatus.REVIEWED for source in sources
        )
        updated, step = self._state_machine.transition(
            current,
            ContentJobState.STRATEGIZING,
            event="research_completed",
            details={
                "source_count": len(sources),
                "reviewed_source_count": reviewed_count,
                "validation": "passed",
            },
        )
        self._repository.save(updated, step)
        return ResearchCompletionOutcome(
            job=updated, validation=validation, paused=False
        )

    def generate_content_strategy(self, job_id: str) -> StrategyGenerationOutcome:
        current = self._repository.get(job_id)
        self._require_state(
            current, ContentJobState.STRATEGIZING, "generate content strategy"
        )
        sources = self._repository.list_sources(job_id)
        research_validation = validate_research_sources(sources)
        if not research_validation.is_valid:
            paused = self._pause_for_controlled_intervention(
                current,
                reason="research evidence is not ready",
                stage="content_strategy",
                details={"issue_codes": self._issue_codes(research_validation)},
            )
            return StrategyGenerationOutcome(
                job=paused,
                strategy=None,
                validation=research_validation,
                paused=True,
            )

        reviewed_sources = tuple(
            source
            for source in sources
            if source.evidence_status is EvidenceStatus.REVIEWED
        )
        request = AIRequest(
            task=AITask.CONTENT_STRATEGY,
            prompt=(
                "Create a content strategy using only the supplied evidence. "
                "Every key message must cite one or more supplied source IDs."
            ),
            context={
                "idea": current.idea,
                "sources": [self._source_ai_context(source) for source in reviewed_sources],
            },
            max_output_characters=6_000,
            output_schema=AIOutputSchema.CONTENT_STRATEGY_V1,
        )
        response, duration_ms, paused = self._generate_structured(
            current, request, stage="content_strategy"
        )
        if paused is not None:
            return StrategyGenerationOutcome(
                job=paused,
                strategy=None,
                validation=ValidationResult(),
                paused=True,
            )
        assert response is not None

        try:
            strategy = self._build_strategy(current, request, response, duration_ms)
        except StructuredOutputError as exc:
            return self._invalid_strategy_outcome(current, exc.code)
        except ValueError:
            return self._invalid_strategy_outcome(current, "invalid_domain_output")

        validation = validate_strategy(strategy, sources)
        if not validation.is_valid:
            paused_job = self._pause_for_controlled_intervention(
                current,
                reason="content strategy validation failed",
                stage="content_strategy",
                details={"issue_codes": self._issue_codes(validation)},
            )
            return StrategyGenerationOutcome(
                job=paused_job,
                strategy=None,
                validation=validation,
                paused=True,
            )

        updated, step = self._state_machine.transition(
            current,
            ContentJobState.GENERATING_MASTER,
            event="content_strategy_persisted",
            details={
                "artifact_id": strategy.id,
                **dict(response.trace_metadata()),
                "validation": "passed",
                "referenced_source_count": len(strategy.supporting_source_ids),
                "duration_ms": duration_ms,
            },
        )
        self._repository.save_strategy_with_checkpoint(strategy, updated, step)
        return StrategyGenerationOutcome(
            job=updated,
            strategy=strategy,
            validation=validation,
            paused=False,
        )

    def generate_master_content(self, job_id: str) -> MasterContentGenerationOutcome:
        current = self._repository.get(job_id)
        self._require_state(
            current, ContentJobState.GENERATING_MASTER, "generate master content"
        )
        try:
            strategy = self._repository.get_strategy(job_id)
        except ArtifactNotFoundError:
            return self._fail_for_missing_strategy(current)

        sources = self._repository.list_sources(job_id)
        request = AIRequest(
            task=AITask.MASTER_CONTENT,
            prompt=(
                "Create canonical master content from the supplied strategy and evidence. "
                "Cite only supplied source IDs; do not create platform-specific posts."
            ),
            context={
                "idea": current.idea,
                "strategy": self._strategy_ai_context(strategy),
                "sources": [
                    self._source_ai_context(source)
                    for source in sources
                    if source.id in strategy.supporting_source_ids
                ],
            },
            max_output_characters=30_000,
            output_schema=AIOutputSchema.MASTER_CONTENT_V1,
        )
        response, duration_ms, paused = self._generate_structured(
            current, request, stage="master_content"
        )
        if paused is not None:
            return MasterContentGenerationOutcome(
                job=paused,
                master_content=None,
                validation=ValidationResult(),
                paused=True,
            )
        assert response is not None

        try:
            master_content = self._build_master_content(
                current, request, response, duration_ms
            )
        except StructuredOutputError as exc:
            return self._invalid_master_outcome(current, exc.code)
        except ValueError:
            return self._invalid_master_outcome(current, "invalid_domain_output")

        validation = validate_master_content(master_content, strategy, sources)
        if not validation.is_valid:
            paused_job = self._pause_for_controlled_intervention(
                current,
                reason="master content validation failed",
                stage="master_content",
                details={"issue_codes": self._issue_codes(validation)},
            )
            return MasterContentGenerationOutcome(
                job=paused_job,
                master_content=None,
                validation=validation,
                paused=True,
            )

        updated, step = self._state_machine.transition(
            current,
            ContentJobState.ADAPTING_PLATFORMS,
            event="master_content_persisted",
            details={
                "artifact_id": master_content.id,
                **dict(response.trace_metadata()),
                "validation": "passed",
                "referenced_source_count": len(master_content.source_ids),
                "duration_ms": duration_ms,
            },
        )
        self._repository.save_master_content_with_checkpoint(
            master_content, updated, step
        )
        return MasterContentGenerationOutcome(
            job=updated,
            master_content=master_content,
            validation=validation,
            paused=False,
        )

    def _build_strategy(
        self,
        current: ContentJob,
        request: AIRequest,
        response: AIResponse,
        duration_ms: int,
    ) -> ContentStrategy:
        if response.structured_output is None:
            raise StructuredOutputError(
                "missing_structured_output",
                "provider returned no structured content strategy",
            )
        output = ContentStrategyOutput.from_mapping(response.structured_output)
        timestamp = self._clock()
        return ContentStrategy(
            id=self._id_factory(),
            job_id=current.id,
            objective=output.objective,
            target_audience=output.target_audience,
            angle=output.angle,
            tone=output.tone,
            key_messages=tuple(
                StrategyKeyMessage(message=item.message, source_ids=item.source_ids)
                for item in output.key_messages
            ),
            intended_outcome=output.intended_outcome,
            created_at=timestamp,
            updated_at=timestamp,
            generation_metadata=self._generation_metadata(
                request, response, timestamp, duration_ms
            ),
        )

    def _build_master_content(
        self,
        current: ContentJob,
        request: AIRequest,
        response: AIResponse,
        duration_ms: int,
    ) -> MasterContent:
        if response.structured_output is None:
            raise StructuredOutputError(
                "missing_structured_output",
                "provider returned no structured master content",
            )
        output = MasterContentOutput.from_mapping(response.structured_output)
        timestamp = self._clock()
        return MasterContent(
            id=self._id_factory(),
            job_id=current.id,
            title=output.title,
            summary=output.summary,
            body=output.body,
            key_points=output.key_points,
            source_ids=output.source_ids,
            created_at=timestamp,
            updated_at=timestamp,
            generation_metadata=self._generation_metadata(
                request, response, timestamp, duration_ms
            ),
        )

    @staticmethod
    def _generation_metadata(
        request: AIRequest,
        response: AIResponse,
        timestamp: datetime,
        duration_ms: int,
    ) -> GenerationMetadata:
        usage = response.usage
        return GenerationMetadata(
            provider=response.provider,
            model=response.model,
            task=request.task.value,
            generated_at=timestamp,
            duration_ms=duration_ms,
            requested_at=usage.requested_at if usage is not None else None,
            provider_latency_ms=usage.latency_ms if usage is not None else None,
            retry_count=usage.retry_count if usage is not None else 0,
            input_tokens=usage.input_tokens if usage is not None else None,
            output_tokens=usage.output_tokens if usage is not None else None,
            total_tokens=usage.total_tokens if usage is not None else None,
            estimated_cost=usage.estimated_cost if usage is not None else None,
            cost_class=usage.cost_class.value if usage is not None else "unknown",
        )

    def _generate_structured(
        self,
        current: ContentJob,
        request: AIRequest,
        *,
        stage: str,
    ) -> tuple[AIResponse | None, int, ContentJob | None]:
        if self._ai_router is None:
            raise ContentIntelligenceConfigurationError("AI router is not configured")
        started_at = self._timer()
        try:
            response = self._ai_router.generate(request)
        except AIUnavailableError as exc:
            duration_ms = max(0, int((self._timer() - started_at) * 1_000))
            attempts = [
                {
                    "provider": item.provider,
                    "outcome": item.outcome,
                    "retry_count": item.retry_count,
                }
                for item in exc.attempts
            ]
            paused = self._pause_for_controlled_intervention(
                current,
                reason="AI providers unavailable under current policy",
                stage=stage,
                details={
                    "task": request.task.value,
                    "attempts": attempts,
                    "duration_ms": duration_ms,
                },
            )
            return None, duration_ms, paused
        duration_ms = max(0, int((self._timer() - started_at) * 1_000))
        return response, duration_ms, None

    def _fail_for_missing_strategy(
        self, current: ContentJob
    ) -> MasterContentGenerationOutcome:
        validation = ValidationResult(
            (
                ValidationIssue(
                    code="content_strategy_missing",
                    message="a persisted content strategy is required",
                ),
            )
        )
        failed, step = self._state_machine.fail(
            current,
            reason="required content strategy is missing",
            details={"stage": "master_content"},
        )
        self._repository.save(failed, step)
        return MasterContentGenerationOutcome(
            job=failed,
            master_content=None,
            validation=validation,
            paused=False,
        )

    def _invalid_strategy_outcome(
        self, current: ContentJob, error_code: str
    ) -> StrategyGenerationOutcome:
        validation = self._structured_validation(
            error_code, "structured content strategy output is invalid"
        )
        paused = self._pause_for_controlled_intervention(
            current,
            reason="structured content strategy output is invalid",
            stage="content_strategy",
            details={"error_code": error_code},
        )
        return StrategyGenerationOutcome(
            job=paused,
            strategy=None,
            validation=validation,
            paused=True,
        )

    def _invalid_master_outcome(
        self, current: ContentJob, error_code: str
    ) -> MasterContentGenerationOutcome:
        validation = self._structured_validation(
            error_code, "structured master content output is invalid"
        )
        paused = self._pause_for_controlled_intervention(
            current,
            reason="structured master content output is invalid",
            stage="master_content",
            details={"error_code": error_code},
        )
        return MasterContentGenerationOutcome(
            job=paused,
            master_content=None,
            validation=validation,
            paused=True,
        )

    def _pause_for_controlled_intervention(
        self,
        current: ContentJob,
        *,
        reason: str,
        stage: str,
        details: Mapping[str, Any] | None = None,
    ) -> ContentJob:
        paused, step = self._state_machine.pause(
            current,
            reason=reason,
            details={"stage": stage, **(details or {})},
        )
        self._repository.save(paused, step)
        return paused

    @staticmethod
    def _structured_validation(error_code: str, message: str) -> ValidationResult:
        return ValidationResult((ValidationIssue(code=error_code, message=message),))

    @staticmethod
    def _require_state(
        job: ContentJob, expected: ContentJobState, action: str
    ) -> None:
        if job.state is not expected:
            raise ValueError(f"cannot {action} while job is in state {job.state}")

    @staticmethod
    def _issue_codes(validation: ValidationResult) -> list[str]:
        return [issue.code for issue in validation.issues]

    @staticmethod
    def _source_ai_context(source: SourceEvidence) -> dict[str, Any]:
        return {
            "id": source.id,
            "title": source.title,
            "url": source.url,
            "source_type": source.source_type.value,
            "relevant_excerpt": source.relevant_excerpt,
        }

    @staticmethod
    def _strategy_ai_context(strategy: ContentStrategy) -> dict[str, Any]:
        return {
            "objective": strategy.objective,
            "target_audience": strategy.target_audience,
            "angle": strategy.angle,
            "tone": strategy.tone,
            "key_messages": [
                {"message": item.message, "source_ids": item.source_ids}
                for item in strategy.key_messages
            ],
            "intended_outcome": strategy.intended_outcome,
        }
