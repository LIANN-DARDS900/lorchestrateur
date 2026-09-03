"""Bounded application-level execution of the existing governed workflow stages."""

from __future__ import annotations

from dataclasses import dataclass

from lorchestrateur.application.service import OrchestrationService
from lorchestrateur.domain.workflow import ContentJob, ContentJobState
from lorchestrateur.persistence.contracts import ContentIntelligenceRepository


class WorkflowExecutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class WorkflowExecutionResult:
    job: ContentJob
    executed_stages: tuple[str, ...]


class ContentWorkflowExecutor:
    """Drives explicit service methods until the workflow reaches a user boundary."""

    _STOP_STATES = frozenset(
        {
            ContentJobState.AWAITING_APPROVAL,
            ContentJobState.APPROVED,
            ContentJobState.PAUSED,
            ContentJobState.FAILED,
        }
    )

    def __init__(
        self,
        service: OrchestrationService,
        repository: ContentIntelligenceRepository,
        *,
        maximum_stage_calls: int = 12,
    ) -> None:
        if maximum_stage_calls < 1:
            raise ValueError("maximum_stage_calls must be positive")
        self._service = service
        self._repository = repository
        self._maximum_stage_calls = maximum_stage_calls

    def run(
        self, job_id: str, *, human_revision_guidance: str | None = None
    ) -> WorkflowExecutionResult:
        stages: list[str] = []
        guidance = human_revision_guidance
        for _ in range(self._maximum_stage_calls):
            current = self._repository.get(job_id)
            if current.state in self._STOP_STATES:
                return WorkflowExecutionResult(current, tuple(stages))
            if current.state is ContentJobState.RESEARCHING:
                self._service.complete_research(job_id)
                stages.append("research")
            elif current.state is ContentJobState.STRATEGIZING:
                self._service.generate_content_strategy(job_id)
                stages.append("strategy")
            elif current.state is ContentJobState.GENERATING_MASTER:
                self._service.generate_master_content(job_id)
                stages.append("master_content")
            elif current.state is ContentJobState.ADAPTING_PLATFORMS:
                self._service.adapt_platforms(job_id, human_guidance=guidance)
                guidance = None
                stages.append("platform_adaptation")
            elif current.state is ContentJobState.VALIDATING:
                self._service.evaluate_platform_adaptations(job_id)
                stages.append("quality_validation")
            else:
                raise WorkflowExecutionError(
                    f"workflow execution is not supported from state {current.state}"
                )
        raise WorkflowExecutionError("bounded workflow execution limit was reached")
