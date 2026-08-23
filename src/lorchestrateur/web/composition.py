"""Application composition root for web, persistence, and governed AI adapters."""

from __future__ import annotations

from dataclasses import dataclass

from lorchestrateur.ai.factory import create_ai_router
from lorchestrateur.ai.router import AIRouter
from lorchestrateur.application.execution import ContentWorkflowExecutor
from lorchestrateur.application.service import OrchestrationService
from lorchestrateur.config import Settings
from lorchestrateur.domain.platform_content import QualityPolicy
from lorchestrateur.domain.workflow import StateMachine
from lorchestrateur.persistence.contracts import ContentIntelligenceRepository
from lorchestrateur.persistence.sqlite import SQLiteContentJobRepository
from lorchestrateur.platforms.builtins import create_default_registry
from lorchestrateur.web.demo import create_demo_provider


@dataclass(frozen=True, slots=True)
class WebComponents:
    repository: ContentIntelligenceRepository
    service: OrchestrationService
    executor: ContentWorkflowExecutor


def compose_web_components(
    settings: Settings,
    *,
    repository: ContentIntelligenceRepository | None = None,
) -> WebComponents:
    registry = create_default_registry()
    selected_repository = repository or SQLiteContentJobRepository.from_database_url(
        settings.database_url,
        platform_registry=registry,
    )
    if settings.app_ai_mode == "demo":
        router = AIRouter(
            (create_demo_provider(),),
            provider_order=("demo",),
            allow_paid_ai=False,
        )
    else:
        router = create_ai_router(settings)
    service = OrchestrationService(
        selected_repository,
        StateMachine(),
        registry,
        ai_router=router,
        quality_policy=QualityPolicy(settings.platform_min_quality_score),
    )
    return WebComponents(
        repository=selected_repository,
        service=service,
        executor=ContentWorkflowExecutor(service, selected_repository),
    )
