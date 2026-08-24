"""Application composition root for web, persistence, and governed AI adapters."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from lorchestrateur.ai.factory import create_ai_router
from lorchestrateur.ai.router import AIRouter
from lorchestrateur.analytics.factory import create_analytics_registry
from lorchestrateur.analytics.registry import AnalyticsRegistry
from lorchestrateur.analytics.service import AnalyticsPolicy, AnalyticsService
from lorchestrateur.application.execution import ContentWorkflowExecutor
from lorchestrateur.application.service import OrchestrationService
from lorchestrateur.config import Settings
from lorchestrateur.domain.learning import LearningMode
from lorchestrateur.domain.platform_content import QualityPolicy
from lorchestrateur.domain.workflow import StateMachine
from lorchestrateur.learning.service import LearningPolicy, LearningService
from lorchestrateur.persistence.contracts import LearningRepository
from lorchestrateur.persistence.sqlite import SQLiteContentJobRepository
from lorchestrateur.platforms.builtins import create_default_registry
from lorchestrateur.publishing.factory import create_publishing_registry
from lorchestrateur.publishing.registry import PublishingRegistry
from lorchestrateur.publishing.service import PublicationPolicy, PublicationService
from lorchestrateur.web.demo import create_demo_provider


@dataclass(frozen=True, slots=True)
class WebComponents:
    repository: LearningRepository
    service: OrchestrationService
    executor: ContentWorkflowExecutor
    publishing_registry: PublishingRegistry
    publication_service: PublicationService
    analytics_registry: AnalyticsRegistry
    analytics_service: AnalyticsService
    learning_service: LearningService


def compose_web_components(
    settings: Settings,
    *,
    repository: LearningRepository | None = None,
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
    learning_service = LearningService(
        selected_repository,
        LearningPolicy(
            enabled=settings.learning_enabled,
            apply_accepted_learning=settings.learning_apply_enabled,
            mode=LearningMode(settings.learning_mode),
            minimum_sample_size=settings.learning_min_sample_size,
            minimum_effect_percent=Decimal(settings.learning_min_effect_percent),
            max_evidence_age_days=settings.learning_max_evidence_age_days,
            recommendation_ttl_days=settings.learning_recommendation_ttl_days,
            window_tolerance_hours=settings.learning_window_tolerance_hours,
        ),
    )
    service = OrchestrationService(
        selected_repository,
        StateMachine(),
        registry,
        ai_router=router,
        quality_policy=QualityPolicy(settings.platform_min_quality_score),
        learning_context_provider=learning_service.strategy_context_for_job,
    )
    publishing_registry = create_publishing_registry(settings)
    publication_service = PublicationService(
        selected_repository,
        publishing_registry,
        StateMachine(),
        PublicationPolicy(
            external_delivery_enabled=settings.publishing_enabled,
            dry_run=settings.publishing_dry_run,
            demo_mode=settings.publishing_adapter_mode == "demo",
            minimum_quality_score=settings.platform_min_quality_score,
            max_retries=settings.publishing_max_retries,
            lease_seconds=settings.publishing_lease_seconds,
        ),
    )
    analytics_registry = create_analytics_registry(settings)
    analytics_service = AnalyticsService(
        selected_repository,
        analytics_registry,
        AnalyticsPolicy(
            external_collection_enabled=settings.analytics_enabled,
            demo_mode=settings.analytics_adapter_mode == "demo",
            max_retries=settings.analytics_max_retries,
            minimum_refresh_seconds=settings.analytics_min_refresh_seconds,
            collection_offsets_hours=settings.analytics_collection_offsets_hours,
            retention_days=settings.analytics_retention_days,
            stale_after_seconds=settings.analytics_stale_after_seconds,
        ),
    )
    return WebComponents(
        repository=selected_repository,
        service=service,
        executor=ContentWorkflowExecutor(service, selected_repository),
        publishing_registry=publishing_registry,
        publication_service=publication_service,
        analytics_registry=analytics_registry,
        analytics_service=analytics_service,
        learning_service=learning_service,
    )
