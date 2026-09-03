"""Framework-neutral preparation facade for the automation-first experience."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lorchestrateur.application.service import OrchestrationService
from lorchestrateur.domain.content import EvidenceStatus
from lorchestrateur.domain.workflow import ContentJob
from lorchestrateur.learning.service import LearningService
from lorchestrateur.persistence.contracts import ArtifactNotFoundError, AutomationRepository

SUPPORTED_PLATFORMS = frozenset({"blog", "x", "instagram", "facebook"})


@dataclass(frozen=True, slots=True)
class QuickCreateRequest:
    workspace_id: str
    idea: str
    target_platforms: tuple[str, ...] = ()
    objective: str | None = None
    audience: str | None = None
    tone: str | None = None
    cta: str | None = None
    topic_category: str | None = None
    use_learning: bool | None = None
    x_format: str = "auto"


@dataclass(frozen=True, slots=True)
class ResolvedAutomationContext:
    workspace_id: str
    target_platforms: tuple[str, ...]
    objective: str
    audience: str
    tone: str
    cta: str | None
    topic_category: str
    use_learning: bool
    explicit_constraints: Mapping[str, Any]
    reusable_knowledge_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AutomationStartResult:
    job: ContentJob
    context: ResolvedAutomationContext
    reused_source_count: int
    ready_to_execute: bool




class AutomationFacade:
    """Resolves context and starts the existing workflow without doing generation itself."""

    def __init__(
        self,
        repository: AutomationRepository,
        orchestration: OrchestrationService,
        learning: LearningService,
    ) -> None:
        self._repository = repository
        self._orchestration = orchestration
        self._learning = learning

    def prepare(self, request: QuickCreateRequest) -> AutomationStartResult:
        profile = self._repository.get_workspace_profile(request.workspace_id)
        idea = request.idea.strip()
        if len(idea) < 10 or len(idea) > 5000:
            raise ValueError("idea must contain between 10 and 5000 characters")
        platforms = (
            tuple(dict.fromkeys(item.strip().lower() for item in request.target_platforms))
            or profile.default_platforms
        )
        if not platforms or set(platforms) - SUPPORTED_PLATFORMS:
            raise ValueError("target platforms contain an unsupported value")
        if request.x_format not in {"auto", "single_post", "thread"}:
            raise ValueError("x format is invalid")

        objective = self._choice(request.objective, profile.default_objective)
        audience = self._choice(request.audience, profile.default_audience)
        tone = self._choice(request.tone, profile.default_tone)
        topic = self._choice(request.topic_category, profile.default_topic_category).lower()
        cta = request.cta.strip() if request.cta and request.cta.strip() else profile.default_cta
        use_learning = (
            self._learning.policy.enabled
            if request.use_learning is None
            else request.use_learning and self._learning.policy.enabled
        )
        constraints: dict[str, Any] = {
            "audience": audience,
            "tone": tone,
            "cta": cta,
            "business_constraints": profile.business_constraints,
            "forbidden_claims": profile.forbidden_claims,
            "uncertain_claims": profile.uncertain_claims,
        }
        if "x" in platforms:
            constraints["x_format"] = request.x_format

        eligible = ()
        if profile.reuse_approved_knowledge:
            eligible = tuple(
                item
                for item in self._repository.list_workspace_knowledge(
                    profile.id, reusable_only=True, active_only=True
                )
                if item.eligible_for_reuse
            )
        job = self._orchestration.create_job(
            workspace_id=profile.id,
            idea=idea,
            target_platforms=platforms,
        )
        self._learning.configure_job(
            job,
            topic_category=topic,
            objective=objective,
            use_learning=use_learning,
            explicit_constraints=constraints,
        )
        job = self._orchestration.begin_research(job.id)
        for item in eligible:
            self._orchestration.add_source(
                job.id,
                title=item.title,
                relevant_excerpt=item.relevant_excerpt,
                source_type=item.source_type,
                url=item.url,
                evidence_status=EvidenceStatus.REVIEWED,
                metadata={
                    "knowledge_item_id": item.id,
                    "workspace_id": profile.id,
                    "provenance": "approved_workspace_knowledge",
                },
            )
        context = ResolvedAutomationContext(
            workspace_id=profile.id,
            target_platforms=platforms,
            objective=objective,
            audience=audience,
            tone=tone,
            cta=cta,
            topic_category=topic,
            use_learning=use_learning,
            explicit_constraints=constraints,
            reusable_knowledge_ids=tuple(item.id for item in eligible),
        )
        return AutomationStartResult(
            job=self._repository.get(job.id),
            context=context,
            reused_source_count=len(eligible),
            ready_to_execute=bool(eligible),
        )

    @staticmethod
    def _choice(explicit: str | None, default: str) -> str:
        return explicit.strip() if explicit and explicit.strip() else default


class StrategyContextProvider:
    """Combines stable workspace constraints with optional approved learning."""

    def __init__(self, repository: AutomationRepository, learning: LearningService) -> None:
        self._repository = repository
        self._learning = learning

    def __call__(self, job: ContentJob) -> Mapping[str, Any]:
        try:
            profile = self._repository.get_workspace_profile(job.workspace_id)
        except ArtifactNotFoundError:
            # Jobs created by the framework-neutral/CLI APIs before V1.1 do not
            # necessarily have an application profile. Their old behavior remains valid.
            return self._learning.strategy_context_for_job(job)
        job_context = self._repository.get_job_learning_context(job.id)
        explicit = dict(job_context.explicit_constraints) if job_context else {}
        learning = dict(self._learning.strategy_context_for_job(job))
        return {
            "precedence": (
                "explicit_user",
                "workspace_constraints",
                "approved_learning",
                "workspace_defaults",
                "system_defaults",
            ),
            "workspace": {
                "audience": explicit.get("audience", profile.default_audience),
                "objective": job_context.objective if job_context else profile.default_objective,
                "tone": explicit.get("tone", profile.default_tone),
                "cta": explicit.get("cta", profile.default_cta),
                "business_constraints": profile.business_constraints,
                "forbidden_claims": profile.forbidden_claims,
                "uncertain_claims": profile.uncertain_claims,
            },
            "explicit_constraints": explicit,
            "approved_learning": learning,
        }
