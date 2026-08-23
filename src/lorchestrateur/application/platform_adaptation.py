"""Governed Phase 3 platform adaptation, evaluation, repair, and approval gating."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from time import perf_counter
from types import MappingProxyType
from uuid import uuid4

from lorchestrateur.ai.contracts import AIRequest, AIResponse
from lorchestrateur.ai.router import AIRouter, AIUnavailableError
from lorchestrateur.ai.structured import StructuredOutputError
from lorchestrateur.domain.content import GenerationMetadata, MasterContent
from lorchestrateur.domain.platform_content import (
    PlatformContentRecord,
    PlatformValidationStatus,
    QualityPolicy,
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
from lorchestrateur.platforms.contracts import (
    PlatformAdaptationContext,
    PlatformValidationContext,
    RepairContext,
)
from lorchestrateur.platforms.registry import PlatformRegistry


class PlatformAdaptationConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PlatformAdaptationOutcome:
    job: ContentJob
    contents: Mapping[str, PlatformContentRecord]
    issues: Mapping[str, ValidationResult]
    generated_platforms: tuple[str, ...]
    reused_platforms: tuple[str, ...]
    repair_requested: bool
    paused: bool


@dataclass(frozen=True, slots=True)
class PlatformEvaluationOutcome:
    job: ContentJob
    contents: Mapping[str, PlatformContentRecord]
    reports: Mapping[str, ValidationResult]
    repair_requested: bool
    paused: bool


class PlatformAdaptationPipeline:
    """Coordinates registry-owned platform behavior without platform condition chains."""

    def __init__(
        self,
        repository: ContentIntelligenceRepository,
        state_machine: StateMachine,
        platforms: PlatformRegistry,
        *,
        ai_router: AIRouter | None,
        quality_policy: QualityPolicy | None = None,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = lambda: str(uuid4()),
        timer: Callable[[], float] = perf_counter,
    ) -> None:
        self._repository = repository
        self._state_machine = state_machine
        self._platforms = platforms
        self._ai_router = ai_router
        self._quality_policy = quality_policy or QualityPolicy()
        self._clock = clock
        self._id_factory = id_factory
        self._timer = timer

    @property
    def quality_policy(self) -> QualityPolicy:
        return self._quality_policy

    def adapt_platforms(
        self,
        job_id: str,
        *,
        generation_attempt_id: str | None = None,
        human_guidance: str | None = None,
    ) -> PlatformAdaptationOutcome:
        current = self._repository.get(job_id)
        self._require_state(current, ContentJobState.ADAPTING_PLATFORMS, "adapt platform content")
        master_content = self._get_required_master(current)
        sources = self._repository.list_sources(job_id)
        attempt_id = generation_attempt_id or self._logical_attempt_id(current, master_content)
        if not attempt_id.strip():
            raise ValueError("generation_attempt_id cannot be empty")

        latest = self._latest_by_platform(current.id, master_content.id)
        contents: dict[str, PlatformContentRecord] = {}
        issues: dict[str, ValidationResult] = {}
        generated: list[str] = []
        reused: list[str] = []

        for platform_key in current.target_platforms:
            platform = self._platforms.get(platform_key)
            same_attempt = self._repository.get_platform_content_by_attempt(
                current.id, master_content.id, platform_key, attempt_id
            )
            if same_attempt is not None:
                contents[platform_key] = same_attempt
                reused.append(platform_key)
                continue

            previous = latest.get(platform_key)
            if (
                current.repair_attempts > 0
                and previous is not None
                and previous.is_approval_ready(self._quality_policy)
                and not human_guidance
            ):
                contents[platform_key] = previous
                reused.append(platform_key)
                continue

            revision = 1 if previous is None else previous.revision + 1
            repair = (
                self._repair_context(previous, human_guidance=human_guidance)
                if current.repair_attempts
                else None
            )
            request = platform.build_request(
                PlatformAdaptationContext(
                    job=current,
                    master_content=master_content,
                    sources=sources,
                    revision=revision,
                    repair=repair,
                )
            )
            response, duration_ms, paused = self._generate(current, platform_key, request)
            if paused is not None:
                return PlatformAdaptationOutcome(
                    job=paused,
                    contents=MappingProxyType(dict(contents)),
                    issues=MappingProxyType(dict(issues)),
                    generated_platforms=tuple(generated),
                    reused_platforms=tuple(reused),
                    repair_requested=False,
                    paused=True,
                )
            assert response is not None
            try:
                if response.structured_output is None:
                    raise StructuredOutputError(
                        "missing_structured_output",
                        "provider returned no structured platform content",
                    )
                payload = platform.parse_payload(response.structured_output)
            except (StructuredOutputError, ValueError) as exc:
                error_code = (
                    exc.code if isinstance(exc, StructuredOutputError) else "invalid_domain_output"
                )
                issue = ValidationResult(
                    (
                        ValidationIssue(
                            code=error_code,
                            message="structured platform output is invalid",
                        ),
                    )
                )
                issues[platform_key] = issue
                current, step = self._state_machine.record_event(
                    current,
                    event="platform_adaptation_rejected",
                    details={
                        "platform": platform_key,
                        "schema_version": platform.schema_version,
                        "error_code": error_code,
                        **dict(response.trace_metadata()),
                        "duration_ms": duration_ms,
                        "revision": revision,
                    },
                )
                self._repository.save(current, step)
                continue

            timestamp = self._clock()
            content = PlatformContentRecord(
                id=self._id_factory(),
                job_id=current.id,
                master_content_id=master_content.id,
                platform=platform_key,
                format=payload.format,
                schema_version=payload.schema_version,
                payload=payload,
                generation_metadata=self._generation_metadata(
                    request, response, timestamp, duration_ms
                ),
                generation_attempt_id=attempt_id,
                validation_status=PlatformValidationStatus.PENDING,
                quality_score=None,
                quality_breakdown=None,
                validation_issues=(),
                revision=revision,
                created_at=timestamp,
                updated_at=timestamp,
            )
            current, step = self._state_machine.record_event(
                current,
                event="platform_content_persisted",
                details={
                    "artifact_id": content.id,
                    "platform": content.platform,
                    "format": content.format,
                    "schema_version": content.schema_version,
                    **dict(response.trace_metadata()),
                    "duration_ms": duration_ms,
                    "revision": content.revision,
                },
            )
            self._repository.save_platform_content_with_checkpoint(content, current, step)
            contents[platform_key] = content
            latest[platform_key] = content
            generated.append(platform_key)

        current, step = self._state_machine.transition(
            current,
            ContentJobState.VALIDATING,
            event="platform_adaptation_completed",
            details={
                "generation_attempt_id": attempt_id,
                "generated_platform_count": len(generated),
                "reused_platform_count": len(reused),
                "structured_failure_count": len(issues),
            },
        )
        self._repository.save(current, step)

        if issues:
            current, step = self._state_machine.request_controlled_repair(
                current,
                details={
                    "stage": "platform_adaptation",
                    "issue_codes_by_platform": {
                        key: [issue.code for issue in report.issues]
                        for key, report in issues.items()
                    },
                },
            )
            self._repository.save(current, step)
            return PlatformAdaptationOutcome(
                job=current,
                contents=MappingProxyType(dict(contents)),
                issues=MappingProxyType(dict(issues)),
                generated_platforms=tuple(generated),
                reused_platforms=tuple(reused),
                repair_requested=current.state is ContentJobState.ADAPTING_PLATFORMS,
                paused=current.state is ContentJobState.PAUSED,
            )

        return PlatformAdaptationOutcome(
            job=current,
            contents=MappingProxyType(dict(contents)),
            issues=MappingProxyType({}),
            generated_platforms=tuple(generated),
            reused_platforms=tuple(reused),
            repair_requested=False,
            paused=False,
        )

    def evaluate_platforms(self, job_id: str) -> PlatformEvaluationOutcome:
        current = self._repository.get(job_id)
        self._require_state(current, ContentJobState.VALIDATING, "evaluate platform content")
        master_content = self._get_required_master(current)
        sources = self._repository.list_sources(job_id)
        latest = self._latest_by_platform(current.id, master_content.id)
        validation_context = PlatformValidationContext(
            job=current, master_content=master_content, sources=sources
        )
        reports: dict[str, ValidationResult] = {}
        evaluated: dict[str, PlatformContentRecord] = {}
        not_ready: list[str] = []

        for platform_key in current.target_platforms:
            content = latest.get(platform_key)
            if content is None:
                reports[platform_key] = ValidationResult(
                    (
                        ValidationIssue(
                            code="missing_platform_content",
                            message=f"required {platform_key} content is missing",
                        ),
                    )
                )
                not_ready.append(platform_key)
                continue
            platform = self._platforms.get(platform_key)
            validation = platform.validate(content, validation_context)
            breakdown = platform.score(
                content, validation_context, validation, self._quality_policy
            )
            status = (
                PlatformValidationStatus.PASSED
                if validation.is_valid
                else PlatformValidationStatus.FAILED
            )
            evaluated_content = replace(
                content,
                validation_status=status,
                quality_score=breakdown.total,
                quality_breakdown=breakdown,
                validation_issues=validation.issues,
                updated_at=self._clock(),
            )
            evaluated[platform_key] = evaluated_content
            report_issues = list(validation.issues)
            if breakdown.total < self._quality_policy.minimum_score:
                report_issues.append(
                    ValidationIssue(
                        code="quality_below_threshold",
                        field="quality_score",
                        message=(
                            f"quality score {breakdown.total} is below configured minimum "
                            f"{self._quality_policy.minimum_score}"
                        ),
                    )
                )
            reports[platform_key] = ValidationResult(tuple(report_issues))
            if not evaluated_content.is_approval_ready(self._quality_policy):
                not_ready.append(platform_key)

        issue_counts = {key: len(report.issues) for key, report in reports.items()}
        quality_scores = {key: content.quality_score for key, content in evaluated.items()}
        if not not_ready:
            updated, step = self._state_machine.transition(
                current,
                ContentJobState.AWAITING_APPROVAL,
                event="platform_quality_gate_passed",
                details={
                    "issue_counts": issue_counts,
                    "quality_scores": quality_scores,
                    "minimum_quality_score": self._quality_policy.minimum_score,
                    "artifact_ids": [evaluated[key].id for key in current.target_platforms],
                },
            )
        else:
            updated, step = self._state_machine.request_controlled_repair(
                current,
                details={
                    "stage": "platform_validation",
                    "not_ready_platforms": not_ready,
                    "issue_codes_by_platform": {
                        key: [issue.code for issue in reports[key].issues] for key in not_ready
                    },
                    "quality_scores": quality_scores,
                    "minimum_quality_score": self._quality_policy.minimum_score,
                },
            )
        self._repository.save_platform_evaluations_with_checkpoint(
            tuple(evaluated.values()), updated, step
        )
        return PlatformEvaluationOutcome(
            job=updated,
            contents=MappingProxyType(dict(evaluated)),
            reports=MappingProxyType(dict(reports)),
            repair_requested=updated.state is ContentJobState.ADAPTING_PLATFORMS,
            paused=updated.state is ContentJobState.PAUSED,
        )

    def _get_required_master(self, current: ContentJob) -> MasterContent:
        try:
            master_content = self._repository.get_master_content(current.id)
        except ArtifactNotFoundError:
            failed, step = self._state_machine.fail(
                current,
                reason="required master content is missing",
                details={"stage": "platform_adaptation"},
            )
            self._repository.save(failed, step)
            raise
        if master_content.job_id != current.id:
            raise ValueError("master content belongs to a different content job")
        return master_content

    def _latest_by_platform(
        self, job_id: str, master_content_id: str
    ) -> dict[str, PlatformContentRecord]:
        latest: dict[str, PlatformContentRecord] = {}
        for content in self._repository.list_platform_contents(job_id):
            if content.master_content_id != master_content_id:
                continue
            previous = latest.get(content.platform)
            if previous is None or content.revision > previous.revision:
                latest[content.platform] = content
        return latest

    def _generate(
        self, current: ContentJob, platform: str, request: AIRequest
    ) -> tuple[AIResponse | None, int, ContentJob | None]:
        if self._ai_router is None:
            raise PlatformAdaptationConfigurationError("AI router is not configured")
        started_at = self._timer()
        try:
            response = self._ai_router.generate(request)
        except AIUnavailableError as exc:
            duration_ms = max(0, int((self._timer() - started_at) * 1_000))
            attempts = [
                {
                    "provider": attempt.provider,
                    "outcome": attempt.outcome,
                    "retry_count": attempt.retry_count,
                }
                for attempt in exc.attempts
            ]
            paused, step = self._state_machine.pause(
                current,
                reason="AI providers unavailable under current policy",
                details={
                    "stage": "platform_adaptation",
                    "platform": platform,
                    "task": request.task.value,
                    "attempts": attempts,
                    "duration_ms": duration_ms,
                },
            )
            self._repository.save(paused, step)
            return None, duration_ms, paused
        duration_ms = max(0, int((self._timer() - started_at) * 1_000))
        return response, duration_ms, None

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

    @staticmethod
    def _repair_context(
        previous: PlatformContentRecord | None,
        *,
        human_guidance: str | None = None,
    ) -> RepairContext:
        if previous is None:
            return RepairContext(
                issue_codes=("missing_platform_content",),
                human_guidance=human_guidance,
            )
        issue_codes = tuple(issue.code for issue in previous.validation_issues)
        if (
            previous.quality_score is not None
            and previous.quality_breakdown is not None
            and not issue_codes
        ):
            issue_codes = ("quality_below_threshold",)
        return RepairContext(
            issue_codes=issue_codes or ("platform_content_not_ready",),
            quality_score=previous.quality_score,
            quality_breakdown=(
                previous.quality_breakdown.to_mapping()
                if previous.quality_breakdown is not None
                else None
            ),
            human_guidance=human_guidance,
        )

    @staticmethod
    def _logical_attempt_id(job: ContentJob, master_content: MasterContent) -> str:
        return f"{job.id}:{master_content.id}:platform-adaptation:{job.repair_attempts}"

    @staticmethod
    def _require_state(job: ContentJob, expected: ContentJobState, action: str) -> None:
        if job.state is not expected:
            raise ValueError(f"cannot {action} while job is in state {job.state}")
