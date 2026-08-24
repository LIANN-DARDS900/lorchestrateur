import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from lorchestrateur.ai.fake import FakeAIProvider
from lorchestrateur.ai.router import AIRouter
from lorchestrateur.application.service import OrchestrationService
from lorchestrateur.domain.analytics import MetricSnapshot
from lorchestrateur.domain.content import EvidenceStatus, GenerationMetadata, SourceType
from lorchestrateur.domain.learning import (
    CohortDefinition,
    EvidenceStrength,
    LearningAnalysisRun,
    LearningAuditEvent,
    LearningMode,
    LearningRunStatus,
    OptimizationRecommendation,
    PerformanceObservation,
    RecommendationKind,
    RecommendationStatus,
)
from lorchestrateur.domain.platform_content import (
    PlatformContentRecord,
    PlatformValidationStatus,
    QualityBreakdown,
)
from lorchestrateur.domain.publication import (
    PublicationMode,
    PublicationReceipt,
    PublicationRequest,
    PublicationStatus,
)
from lorchestrateur.domain.workflow import ContentJob, StateMachine
from lorchestrateur.learning.service import LearningPolicy, LearningService
from lorchestrateur.learning.statistics import arithmetic_mean, median
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.persistence.sqlite import SQLiteContentJobRepository
from lorchestrateur.platforms.builtins import create_default_registry
from lorchestrateur.platforms.instagram import (
    InstagramCarouselV1,
    InstagramImagePostV1,
    InstagramSlideV1,
)
from lorchestrateur.platforms.x import XContentV1, XPostV1

NOW = datetime(2026, 8, 24, 12, tzinfo=UTC)


def _policy(**overrides):
    values = {
        "enabled": True,
        "mode": LearningMode.DEMO,
        "minimum_sample_size": 3,
        "minimum_effect_percent": Decimal("15"),
    }
    values.update(overrides)
    return LearningPolicy(**values)


def _service(repository=None, **policy_overrides):
    repository = repository or InMemoryContentJobRepository()
    service = LearningService(repository, _policy(**policy_overrides), clock=lambda: NOW)
    return repository, service


def _seed_publication(
    repository,
    service,
    *,
    number,
    format_value,
    metric_value,
    source="demo.analytics.v1",
    topic="operations-it",
    objective="notoriete",
    platform="x",
    workspace_id="workspace-test",
):
    job = ContentJob.create(
        workspace_id=workspace_id,
        idea=f"Idée d’apprentissage {number}",
        target_platforms=(platform,),
        job_id=f"job-{number}",
        now=NOW - timedelta(days=10),
    )
    repository.add(job)
    service.configure_job(
        job,
        topic_category=topic,
        objective=objective,
        use_learning=True,
        explicit_constraints={},
    )
    if platform == "x":
        payload = XContentV1(
            format=format_value,
            opening_hook="Accroche",
            posts=(XPostV1(1, "Message gouverné"),),
            cta=None,
            source_ids=("source-1",),
        )
        metric_key = "x.impressions"
    elif format_value == "carousel":
        payload = InstagramCarouselV1(
            hook="Accroche",
            slides=(
                InstagramSlideV1(1, "Premier", "Message"),
                InstagramSlideV1(2, "Second", "Message"),
            ),
            caption="Légende",
            cta=None,
            source_ids=("source-1",),
        )
        metric_key = "instagram.saves"
    else:
        payload = InstagramImagePostV1(
            hook="Accroche",
            visual_concept="Concept visuel",
            caption="Légende",
            cta=None,
            source_ids=("source-1",),
        )
        metric_key = "instagram.saves"
    content = PlatformContentRecord(
        id=f"content-{number}",
        job_id=job.id,
        master_content_id=f"master-{number}",
        platform=platform,
        format=format_value,
        schema_version=payload.schema_version,
        payload=payload,
        generation_metadata=GenerationMetadata(
            provider="demo",
            model="demo-v1",
            task="platform_adaptation",
            generated_at=NOW,
            duration_ms=1,
        ),
        generation_attempt_id=f"attempt-{number}",
        validation_status=PlatformValidationStatus.PASSED,
        quality_score=100,
        quality_breakdown=QualityBreakdown(20, 20, 20, 20, 20),
        validation_issues=(),
        revision=1,
        created_at=NOW,
        updated_at=NOW,
    )
    publication = PublicationRequest(
        id=f"publication-{number}",
        job_id=job.id,
        platform_content_id=content.id,
        platform=platform,
        requested_by="Test",
        mode=PublicationMode.PUBLISH_NOW,
        scheduled_at=None,
        idempotency_key=f"publication-key-{number}",
        status=PublicationStatus.PUBLISHED,
        dry_run=False,
        claim_owner=None,
        claimed_at=None,
        lease_expires_at=None,
        created_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(days=2),
    )
    receipt = PublicationReceipt(
        id=f"receipt-{number}",
        publication_id=publication.id,
        platform=platform,
        item_index=1,
        remote_id=f"remote-{number}",
        remote_url=None,
        published_at=NOW - timedelta(days=2),
        adapter_name="demo.publisher",
        adapter_version="1",
        status="published",
        delivery_kind="demo",
    )
    snapshot = MetricSnapshot(
        id=f"snapshot-{number}-{source}",
        collection_run_id=f"collection-{number}-{source}",
        publication_receipt_id=receipt.id,
        job_id=job.id,
        platform_content_id=content.id,
        platform=platform,
        metric_key=metric_key,
        value=Decimal(metric_value),
        observed_at=receipt.published_at + timedelta(hours=24),
        period_start=None,
        period_end=None,
        source=source,
        source_version="1",
        collected_at=NOW,
    )
    repository._platform_contents[content.id] = content  # noqa: SLF001
    repository._publications[publication.id] = publication  # noqa: SLF001
    repository._publication_receipts[publication.id] = [receipt]  # noqa: SLF001
    repository._publication_attempts[publication.id] = []  # noqa: SLF001
    repository._metric_snapshots[snapshot.id] = snapshot  # noqa: SLF001
    return job, snapshot


def _seed_comparison(
    repository, service, a_values, b_values, *, start=1, workspace_id="workspace-test"
):
    for offset, value in enumerate(a_values, start=start):
        _seed_publication(
            repository,
            service,
            number=f"a-{offset}",
            format_value="single_post",
            metric_value=value,
            workspace_id=workspace_id,
        )
    for offset, value in enumerate(b_values, start=start):
        _seed_publication(
            repository,
            service,
            number=f"b-{offset}",
            format_value="thread",
            metric_value=value,
            workspace_id=workspace_id,
        )


class LearningStatisticsAndServiceTests(unittest.TestCase):
    def test_median_is_outlier_resistant_and_mean_is_transparent(self):
        values = (Decimal(10), Decimal(11), Decimal(12), Decimal(1000))
        self.assertEqual(median(values), Decimal("11.5"))
        self.assertEqual(arithmetic_mean(values), Decimal("258.25"))

    def test_insufficient_samples_create_no_observation_or_recommendation(self):
        repository, service = _service()
        _seed_comparison(repository, service, (100, 110), (200, 210))

        outcome = service.analyze(
            workspace_id="workspace-test",
            platform="x",
            topic_category="operations-it",
            objective="notoriete",
            window_hours=24,
            actor="Analyste",
        )

        self.assertEqual(outcome.run.status, LearningRunStatus.INSUFFICIENT_DATA)
        self.assertIsNone(outcome.observation)
        self.assertIsNone(outcome.recommendation)

    def test_sufficient_comparison_is_deterministic_idempotent_and_provenanced(self):
        repository, service = _service()
        _seed_comparison(repository, service, (100, 105, 110), (210, 220, 230))

        first = service.analyze(
            workspace_id="workspace-test",
            platform="x",
            topic_category="operations-it",
            objective="notoriete",
            window_hours=24,
            actor="Analyste",
        )
        second = service.analyze(
            workspace_id="workspace-test",
            platform="x",
            topic_category="operations-it",
            objective="notoriete",
            window_hours=24,
            actor="Analyste",
        )

        self.assertEqual(first.run.id, second.run.id)
        self.assertEqual(first.observation.median_a, Decimal(105))
        self.assertEqual(first.observation.median_b, Decimal(220))
        self.assertEqual(len(first.observation.snapshot_ids), 6)
        self.assertIn(
            first.observation.evidence_strength,
            {
                EvidenceStrength.WEAK,
                EvidenceStrength.MODERATE,
                EvidenceStrength.STRONG,
            },
        )
        self.assertEqual(first.recommendation.kind, RecommendationKind.TEST_FORMAT)
        self.assertEqual(first.recommendation.parameters["preferred_format"], "thread")
        self.assertEqual(repository.list_learning_profile_entries(), ())

    def test_demo_and_live_sources_never_mix(self):
        repository, service = _service()
        _, demo_snapshot = _seed_publication(
            repository,
            service,
            number="isolation",
            format_value="single_post",
            metric_value=100,
        )
        live_snapshot = replace(
            demo_snapshot,
            id="snapshot-live",
            collection_run_id="collection-live",
            value=Decimal(9999),
            source="x.analytics.v1",
        )
        repository._metric_snapshots[live_snapshot.id] = live_snapshot  # noqa: SLF001
        cohort = CohortDefinition(
            "x", "single_post", "operations-it", "notoriete", "x.impressions", 24
        )

        samples, _ = service._samples("workspace-test", cohort)  # noqa: SLF001

        self.assertEqual(samples[0].value, Decimal(100))

    def test_human_acceptance_opt_out_scope_and_user_constraint_precedence(self):
        repository, service = _service()
        _seed_comparison(repository, service, (100, 105, 110), (210, 220, 230))
        outcome = service.analyze(
            workspace_id="workspace-test",
            platform="x",
            topic_category="operations-it",
            objective="notoriete",
            window_hours=24,
            actor="Analyste",
        )
        service.accept(outcome.recommendation.id, decided_by="Responsable")
        future = ContentJob.create(
            workspace_id="workspace-test",
            idea="Futur contenu",
            target_platforms=("x",),
            job_id="future-job",
            now=NOW,
        )
        repository.add(future)
        service.configure_job(
            future,
            topic_category="operations-it",
            objective="notoriete",
            use_learning=True,
            explicit_constraints={"x_format": "single_post"},
        )
        self.assertEqual(service.strategy_context_for_job(future)["recommendations"], [])

        context = repository.get_job_learning_context(future.id)
        repository.save_job_learning_context(
            replace(context, explicit_constraints={"x_format": "auto"}, updated_at=NOW)
        )
        applied = service.strategy_context_for_job(future)
        self.assertEqual(applied["recommendations"][0]["parameters"]["preferred_format"], "thread")

        repository.save_job_learning_context(
            replace(context, use_learning=False, explicit_constraints={}, updated_at=NOW)
        )
        self.assertEqual(service.strategy_context_for_job(future), {})

    def test_instagram_format_comparison_uses_saves_without_cross_metric_normalization(self):
        repository, service = _service()
        for index, value in enumerate((10, 11, 12), start=1):
            _seed_publication(
                repository,
                service,
                number=f"ig-image-{index}",
                format_value="image_post_concept",
                metric_value=value,
                platform="instagram",
            )
        for index, value in enumerate((30, 32, 34), start=1):
            _seed_publication(
                repository,
                service,
                number=f"ig-carousel-{index}",
                format_value="carousel",
                metric_value=value,
                platform="instagram",
            )

        outcome = service.analyze(
            workspace_id="workspace-test",
            platform="instagram",
            topic_category="operations-it",
            objective="notoriete",
            window_hours=24,
            actor="Analyste",
        )

        self.assertEqual(outcome.observation.metric_key, "instagram.saves")
        self.assertEqual(outcome.recommendation.parameters["preferred_format"], "carousel")

    def test_contradictory_observation_marks_old_profile_outdated_until_human_decides(self):
        repository, service = _service()
        _seed_comparison(repository, service, (100, 105, 110), (210, 220, 230))
        first = service.analyze(
            workspace_id="workspace-test",
            platform="x",
            topic_category="operations-it",
            objective="notoriete",
            window_hours=24,
            actor="Analyste",
        )
        service.accept(first.recommendation.id, decided_by="Responsable")
        for snapshot in tuple(repository._metric_snapshots.values()):  # noqa: SLF001
            replacement_value = 500 if "job-a-" in snapshot.job_id else 80
            replacement = replace(
                snapshot,
                id=f"new-{snapshot.id}",
                collection_run_id=f"new-{snapshot.collection_run_id}",
                value=Decimal(replacement_value),
                observed_at=snapshot.observed_at + timedelta(hours=1),
                collected_at=snapshot.collected_at + timedelta(hours=1),
            )
            repository._metric_snapshots[replacement.id] = replacement  # noqa: SLF001

        second = service.analyze(
            workspace_id="workspace-test",
            platform="x",
            topic_category="operations-it",
            objective="notoriete",
            window_hours=24,
            actor="Analyste",
        )

        old = repository.get_optimization_recommendation(first.recommendation.id)
        self.assertTrue(old.potentially_outdated)
        self.assertEqual(old.status, RecommendationStatus.ACCEPTED)
        self.assertEqual(len(repository.list_learning_profile_entries(active_only=True)), 1)
        self.assertEqual(second.recommendation.parameters["preferred_format"], "single_post")

        service.accept(second.recommendation.id, decided_by="Responsable")
        self.assertEqual(
            repository.get_optimization_recommendation(first.recommendation.id).status,
            RecommendationStatus.SUPERSEDED,
        )
        active = repository.list_learning_profile_entries(active_only=True)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].parameters["preferred_format"], "single_post")

    def test_rejection_expiry_and_contradiction_do_not_silently_change_profile(self):
        repository, service = _service(recommendation_ttl_days=1)
        _seed_comparison(repository, service, (100, 105, 110), (210, 220, 230))
        outcome = service.analyze(
            workspace_id="workspace-test",
            platform="x",
            topic_category="operations-it",
            objective="notoriete",
            window_hours=24,
            actor="Analyste",
        )
        rejected = service.reject(outcome.recommendation.id, decided_by="Responsable")
        self.assertEqual(rejected.status, RecommendationStatus.REJECTED)
        self.assertEqual(repository.list_learning_profile_entries(), ())

        proposed = replace(
            outcome.recommendation,
            id="expired-recommendation",
            observation_id="another-observation",
            status=RecommendationStatus.PROPOSED,
            created_at=NOW - timedelta(days=3),
            expires_at=NOW - timedelta(days=2),
        )
        repository._optimization_recommendations[proposed.id] = proposed  # noqa: SLF001
        self.assertEqual(service.expire_due(), 1)
        self.assertEqual(
            repository.get_optimization_recommendation(proposed.id).status,
            RecommendationStatus.EXPIRED,
        )


class LearningPersistenceAndIntegrationTests(unittest.TestCase):
    def test_sqlite_round_trip_preserves_provenance_profile_and_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.sqlite3"
            repository = SQLiteContentJobRepository(path)
            job = ContentJob.create(
                workspace_id="workspace-test",
                idea="Persistance apprentissage",
                target_platforms=("x",),
                job_id="job-sqlite",
                now=NOW,
            )
            repository.add(job)
            service = LearningService(repository, _policy(), clock=lambda: NOW)
            context = service.configure_job(
                job,
                topic_category="operations-it",
                objective="notoriete",
                use_learning=True,
            )
            cohort_a = CohortDefinition(
                "x", "single_post", "operations-it", "notoriete", "x.impressions", 24
            )
            cohort_b = replace(cohort_a, format="thread")
            run = repository.add_learning_run(
                LearningAnalysisRun(
                    id="run-sqlite",
                    idempotency_key="learning-key-sqlite",
                    workspace_id="workspace-test",
                    mode=LearningMode.DEMO,
                    cohort_a=cohort_a,
                    cohort_b=cohort_b,
                    algorithm_version="v1",
                    minimum_sample_size=3,
                    started_at=NOW,
                    completed_at=NOW,
                    status=LearningRunStatus.SUCCEEDED,
                    sample_count_a=3,
                    sample_count_b=3,
                )
            )
            observation = repository.add_performance_observation(
                PerformanceObservation(
                    id="observation-sqlite",
                    analysis_run_id=run.id,
                    workspace_id="workspace-test",
                    mode=LearningMode.DEMO,
                    platform="x",
                    metric_key="x.impressions",
                    window_hours=24,
                    cohort_a_format="single_post",
                    cohort_b_format="thread",
                    sample_count_a=3,
                    sample_count_b=3,
                    median_a=Decimal(100),
                    median_b=Decimal(200),
                    mean_a=Decimal(100),
                    mean_b=Decimal(200),
                    relative_difference_percent=Decimal(100),
                    evidence_strength=EvidenceStrength.MODERATE,
                    evidence_breakdown={"total": 60},
                    publication_ids=("p1", "p2"),
                    receipt_ids=("r1", "r2"),
                    snapshot_ids=("s1", "s2"),
                    created_at=NOW,
                )
            )
            recommendation = repository.add_optimization_recommendation(
                OptimizationRecommendation(
                    id="recommendation-sqlite",
                    observation_id=observation.id,
                    workspace_id="workspace-test",
                    mode=LearningMode.DEMO,
                    platform="x",
                    topic_category="operations-it",
                    objective="notoriete",
                    kind=RecommendationKind.TEST_FORMAT,
                    parameters={"preferred_format": "thread"},
                    rationale="Tester prudemment le fil sans inférer une causalité.",
                    evidence_strength=EvidenceStrength.MODERATE,
                    status=RecommendationStatus.PROPOSED,
                    created_at=NOW,
                    expires_at=NOW + timedelta(days=30),
                )
            )
            service.accept(recommendation.id, decided_by="Responsable")
            repository.add_learning_event(
                LearningAuditEvent(
                    id="event-sqlite",
                    event="audit_test",
                    entity_type="recommendation",
                    entity_id=recommendation.id,
                    actor="Test",
                    metadata={"count": 2},
                    created_at=NOW,
                )
            )

            reopened = SQLiteContentJobRepository(path)

            self.assertEqual(reopened.get_job_learning_context(job.id), context)
            self.assertEqual(reopened.get_observation_for_run(run.id), observation)
            self.assertEqual(
                reopened.get_optimization_recommendation(recommendation.id).status,
                RecommendationStatus.ACCEPTED,
            )
            self.assertEqual(len(reopened.list_learning_profile_entries(active_only=True)), 1)
            self.assertTrue(
                any(item.event == "audit_test" for item in reopened.list_learning_events())
            )

    def test_only_accepted_learning_reaches_strategy_request(self):
        repository, learning = _service()
        _seed_comparison(repository, learning, (100, 105, 110), (210, 220, 230))
        outcome = learning.analyze(
            workspace_id="workspace-test",
            platform="x",
            topic_category="operations-it",
            objective="notoriete",
            window_hours=24,
            actor="Analyste",
        )
        learning.accept(outcome.recommendation.id, decided_by="Responsable")
        provider = FakeAIProvider(
            structured_output={
                "schema_version": "content_strategy_v1",
                "objective": "Informer",
                "target_audience": "Responsables IT",
                "angle": "Contrôle",
                "tone": "Professionnel",
                "key_messages": [
                    {"message": "Message sourcé", "source_references": ["source-future"]}
                ],
                "intended_outcome": "Compréhension",
            }
        )
        orchestration = OrchestrationService(
            repository,
            StateMachine(clock=lambda: NOW),
            create_default_registry(),
            ai_router=AIRouter((provider,), provider_order=("fake",), allow_paid_ai=False),
            learning_context_provider=learning.strategy_context_for_job,
            clock=lambda: NOW,
        )
        job = orchestration.create_job(
            workspace_id="workspace-test",
            idea="Futur workflow gouverné",
            target_platforms=("x",),
            job_id="future-strategy",
        )
        learning.configure_job(
            job,
            topic_category="operations-it",
            objective="notoriete",
            use_learning=True,
            explicit_constraints={"x_format": "auto"},
        )
        orchestration.begin_research(job.id)
        orchestration.add_source(
            job.id,
            source_id="source-future",
            title="Source",
            source_type=SourceType.MANUAL,
            relevant_excerpt="Information revue pour la stratégie.",
            evidence_status=EvidenceStatus.REVIEWED,
        )
        orchestration.complete_research(job.id)

        orchestration.generate_content_strategy(job.id)

        request = provider.requests[-1]
        self.assertEqual(
            request.context["approved_learning"]["recommendations"][0]["platform"], "x"
        )
        self.assertNotIn("rationale", request.context["approved_learning"]["recommendations"][0])


if __name__ == "__main__":
    unittest.main()
