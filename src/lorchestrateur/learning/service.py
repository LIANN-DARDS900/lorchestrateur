"""Deterministic observations and human-governed optimization profiles."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from lorchestrateur.domain.learning import (
    CohortDefinition,
    JobLearningContext,
    LearningAnalysisRun,
    LearningAuditEvent,
    LearningMode,
    LearningProfile,
    LearningProfileEntry,
    LearningRunStatus,
    OptimizationRecommendation,
    PerformanceObservation,
    RecommendationKind,
    RecommendationStatus,
)
from lorchestrateur.domain.publication import PublicationStatus
from lorchestrateur.domain.workflow import ContentJob
from lorchestrateur.learning.statistics import (
    arithmetic_mean,
    assess_evidence,
    median,
    relative_difference_percent,
)
from lorchestrateur.persistence.contracts import LearningRepository

ALGORITHM_VERSION = "deterministic_cohort_comparison_v1"


@dataclass(frozen=True, slots=True)
class LearningPolicy:
    enabled: bool = False
    apply_accepted_learning: bool = True
    mode: LearningMode = LearningMode.DEMO
    minimum_sample_size: int = 5
    minimum_effect_percent: Decimal = Decimal("15")
    max_evidence_age_days: int = 365
    recommendation_ttl_days: int = 180
    window_tolerance_hours: int = 2

    def __post_init__(self) -> None:
        if self.minimum_sample_size < 2:
            raise ValueError("learning minimum sample size must be at least two")
        if not Decimal(0) <= self.minimum_effect_percent <= Decimal(1000):
            raise ValueError("learning effect threshold is invalid")
        if self.max_evidence_age_days < 1 or self.recommendation_ttl_days < 1:
            raise ValueError("learning age policies must be positive")
        if not 0 <= self.window_tolerance_hours <= 24:
            raise ValueError("learning window tolerance must be between zero and 24 hours")


@dataclass(frozen=True, slots=True)
class LearningAnalysisOutcome:
    run: LearningAnalysisRun
    observation: PerformanceObservation | None
    recommendation: OptimizationRecommendation | None


@dataclass(frozen=True, slots=True)
class _CohortSample:
    publication_id: str
    receipt_ids: tuple[str, ...]
    snapshot_ids: tuple[str, ...]
    value: Decimal
    exact_window: bool


SUPPORTED_COMPARISONS: Mapping[str, tuple[str, str, str]] = {
    "x": ("single_post", "thread", "x.impressions"),
    "instagram": ("image_post_concept", "carousel", "instagram.saves"),
}


class LearningService:
    def __init__(
        self,
        repository: LearningRepository,
        policy: LearningPolicy,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = lambda: str(uuid4()),
    ) -> None:
        self._repository = repository
        self.policy = policy
        self._clock = clock
        self._id_factory = id_factory

    def configure_job(
        self,
        job: ContentJob,
        *,
        topic_category: str,
        objective: str,
        use_learning: bool,
        explicit_constraints: Mapping[str, Any] | None = None,
    ) -> JobLearningContext:
        now = self._clock()
        context = JobLearningContext(
            job_id=job.id,
            workspace_id=job.workspace_id,
            topic_category=topic_category,
            objective=objective,
            use_learning=use_learning,
            mode=self.policy.mode,
            explicit_constraints=explicit_constraints or {},
            applied_profile_entry_ids=(),
            created_at=now,
            updated_at=now,
        )
        self._repository.save_job_learning_context(context)
        self._event(
            "learning_context_configured",
            "job",
            job.id,
            "application",
            {
                "mode": context.mode.value,
                "use_learning": context.use_learning,
                "constraint_keys": sorted(context.explicit_constraints),
            },
        )
        return context

    def analyze(
        self,
        *,
        workspace_id: str,
        platform: str,
        topic_category: str,
        objective: str,
        window_hours: int,
        actor: str,
    ) -> LearningAnalysisOutcome:
        if not self.policy.enabled:
            raise ValueError("learning analysis is disabled by policy")
        normalized_platform = platform.strip().lower()
        try:
            format_a, format_b, metric_key = SUPPORTED_COMPARISONS[normalized_platform]
        except KeyError as exc:
            raise ValueError("no transparent comparison is defined for this platform") from exc
        cohort_a = CohortDefinition(
            normalized_platform, format_a, topic_category, objective, metric_key, window_hours
        )
        cohort_b = CohortDefinition(
            normalized_platform, format_b, topic_category, objective, metric_key, window_hours
        )
        samples_a, eligible_a = self._samples(workspace_id, cohort_a)
        samples_b, eligible_b = self._samples(workspace_id, cohort_b)
        sample_identity = sorted(
            snapshot_id
            for sample in (*samples_a, *samples_b)
            for snapshot_id in sample.snapshot_ids
        )
        digest = hashlib.sha256("|".join(sample_identity).encode("utf-8")).hexdigest()[:20]
        key = ":".join(
            (
                ALGORITHM_VERSION,
                workspace_id,
                self.policy.mode.value,
                normalized_platform,
                cohort_a.topic_category,
                cohort_a.objective,
                str(window_hours),
                digest,
            )
        )
        existing = self._repository.get_learning_run_by_idempotency_key(key)
        if existing is not None and existing.status is not LearningRunStatus.RUNNING:
            return LearningAnalysisOutcome(
                existing,
                self._repository.get_observation_for_run(existing.id),
                self._repository.get_recommendation_for_run(existing.id),
            )
        now = self._clock()
        status = (
            LearningRunStatus.SUCCEEDED
            if min(len(samples_a), len(samples_b)) >= self.policy.minimum_sample_size
            else LearningRunStatus.INSUFFICIENT_DATA
        )
        run = existing or LearningAnalysisRun(
            id=self._id_factory(),
            idempotency_key=key,
            workspace_id=workspace_id,
            mode=self.policy.mode,
            cohort_a=cohort_a,
            cohort_b=cohort_b,
            algorithm_version=ALGORITHM_VERSION,
            minimum_sample_size=self.policy.minimum_sample_size,
            started_at=now,
            completed_at=now,
            status=status,
            sample_count_a=len(samples_a),
            sample_count_b=len(samples_b),
        )
        run = self._repository.add_learning_run(run)
        self._event(
            "learning_analysis_completed",
            "learning_run",
            run.id,
            actor,
            {
                "status": status.value,
                "platform": normalized_platform,
                "sample_count_a": len(samples_a),
                "sample_count_b": len(samples_b),
            },
        )
        if status is LearningRunStatus.INSUFFICIENT_DATA:
            return LearningAnalysisOutcome(run, None, None)

        values_a = tuple(item.value for item in samples_a)
        values_b = tuple(item.value for item in samples_b)
        exact_window = all(item.exact_window for item in (*samples_a, *samples_b))
        assessment = assess_evidence(
            values_a,
            values_b,
            minimum_sample_size=self.policy.minimum_sample_size,
            eligible_publications_a=eligible_a,
            eligible_publications_b=eligible_b,
            exact_window=exact_window,
        )
        median_a = median(values_a)
        median_b = median(values_b)
        difference = relative_difference_percent(median_a, median_b)
        observation = self._repository.add_performance_observation(
            PerformanceObservation(
                id=self._id_factory(),
                analysis_run_id=run.id,
                workspace_id=workspace_id,
                mode=self.policy.mode,
                platform=normalized_platform,
                metric_key=metric_key,
                window_hours=window_hours,
                cohort_a_format=format_a,
                cohort_b_format=format_b,
                sample_count_a=len(values_a),
                sample_count_b=len(values_b),
                median_a=median_a,
                median_b=median_b,
                mean_a=arithmetic_mean(values_a),
                mean_b=arithmetic_mean(values_b),
                relative_difference_percent=difference,
                evidence_strength=assessment.strength,
                evidence_breakdown=assessment.to_mapping(),
                publication_ids=tuple(item.publication_id for item in (*samples_a, *samples_b)),
                receipt_ids=tuple(
                    receipt for item in (*samples_a, *samples_b) for receipt in item.receipt_ids
                ),
                snapshot_ids=tuple(
                    snapshot for item in (*samples_a, *samples_b) for snapshot in item.snapshot_ids
                ),
                created_at=now,
            )
        )
        recommendation = self._propose(observation, cohort_a, cohort_b, actor=actor)
        return LearningAnalysisOutcome(run, observation, recommendation)

    def accept(
        self, recommendation_id: str, *, decided_by: str, reason: str | None = None
    ) -> OptimizationRecommendation:
        recommendation = self._repository.get_optimization_recommendation(recommendation_id)
        now = self._clock()
        if recommendation.expires_at <= now:
            self._expire_recommendation(recommendation, now=now)
            raise ValueError("recommendation has expired")
        accepted = recommendation.decide(
            RecommendationStatus.ACCEPTED,
            decided_by=decided_by,
            reason=reason,
            now=now,
        )
        for existing in self._repository.list_optimization_recommendations(
            workspace_id=accepted.workspace_id, status=RecommendationStatus.ACCEPTED
        ):
            if existing.id == accepted.id or not self._same_scope(existing, accepted):
                continue
            superseded = replace(
                existing,
                status=RecommendationStatus.SUPERSEDED,
                potentially_outdated=True,
                decided_at=now,
                decided_by=decided_by,
                decision_reason="superseded by a newer accepted recommendation",
            )
            self._repository.save_optimization_recommendation(superseded)
            for entry in self._repository.list_learning_profile_entries(
                recommendation_id=existing.id
            ):
                self._repository.save_learning_profile_entry(replace(entry, active=False))
        self._repository.save_optimization_recommendation(accepted)
        profile = self._repository.get_learning_profile(accepted.workspace_id, accepted.mode)
        if profile is None:
            profile = self._repository.add_learning_profile(
                LearningProfile(
                    id=self._id_factory(),
                    workspace_id=accepted.workspace_id,
                    mode=accepted.mode,
                    created_at=now,
                    updated_at=now,
                )
            )
        self._repository.add_learning_profile_entry(
            LearningProfileEntry(
                id=self._id_factory(),
                profile_id=profile.id,
                recommendation_id=accepted.id,
                platform=accepted.platform,
                topic_category=accepted.topic_category,
                objective=accepted.objective,
                kind=accepted.kind,
                parameters=accepted.parameters,
                evidence_strength=accepted.evidence_strength,
                accepted_at=now,
                expires_at=accepted.expires_at,
            )
        )
        self._event(
            "learning_recommendation_accepted",
            "recommendation",
            accepted.id,
            decided_by,
            {
                "profile_id": profile.id,
                "mode": accepted.mode.value,
            },
        )
        return accepted

    def reject(
        self, recommendation_id: str, *, decided_by: str, reason: str | None = None
    ) -> OptimizationRecommendation:
        current = self._repository.get_optimization_recommendation(recommendation_id)
        rejected = current.decide(
            RecommendationStatus.REJECTED,
            decided_by=decided_by,
            reason=reason,
            now=self._clock(),
        )
        self._repository.save_optimization_recommendation(rejected)
        self._event(
            "learning_recommendation_rejected", "recommendation", rejected.id, decided_by, {}
        )
        return rejected

    def expire_due(self) -> int:
        now = self._clock()
        expired = 0
        for recommendation in self._repository.list_optimization_recommendations():
            if recommendation.expires_at > now or recommendation.status not in {
                RecommendationStatus.PROPOSED,
                RecommendationStatus.ACCEPTED,
            }:
                continue
            self._expire_recommendation(recommendation, now=now)
            expired += 1
        return expired

    def strategy_context_for_job(self, job: ContentJob) -> Mapping[str, Any]:
        context = self._repository.get_job_learning_context(job.id)
        if (
            context is None
            or not self.policy.enabled
            or not self.policy.apply_accepted_learning
            or not context.use_learning
            or context.mode is not self.policy.mode
        ):
            return {}
        now = self._clock()
        entries = []
        for entry in self._repository.list_learning_profile_entries(
            workspace_id=job.workspace_id, active_only=True
        ):
            if entry.expires_at <= now:
                self._repository.save_learning_profile_entry(replace(entry, active=False))
                continue
            if (
                entry.platform not in job.target_platforms
                or entry.topic_category != context.topic_category
                or entry.objective != context.objective
            ):
                continue
            explicit = context.explicit_constraints.get(f"{entry.platform}_format")
            recommended = entry.parameters.get("preferred_format")
            if explicit and explicit != "auto" and recommended and explicit != recommended:
                continue
            entries.append(entry)
        applied_ids = tuple(item.id for item in entries)
        if applied_ids != context.applied_profile_entry_ids:
            self._repository.save_job_learning_context(
                context.with_applied_entries(applied_ids, now=now)
            )
            self._event(
                "learning_profile_applied",
                "job",
                job.id,
                "application",
                {"profile_entry_ids": applied_ids, "count": len(applied_ids)},
            )
        return {
            "mode": context.mode.value,
            "topic_category": context.topic_category,
            "objective": context.objective,
            "recommendations": [
                {
                    "profile_entry_id": item.id,
                    "platform": item.platform,
                    "kind": item.kind.value,
                    "parameters": dict(item.parameters),
                    "evidence_strength": item.evidence_strength.value,
                }
                for item in entries
            ],
            "explicit_constraints": dict(context.explicit_constraints),
        }

    def _samples(
        self, workspace_id: str, cohort: CohortDefinition
    ) -> tuple[tuple[_CohortSample, ...], int]:
        now = self._clock()
        oldest = now - timedelta(days=self.policy.max_evidence_age_days)
        samples = []
        eligible = 0
        for publication in self._repository.list_publications():
            if (
                publication.platform != cohort.platform
                or publication.status is not PublicationStatus.PUBLISHED
            ):
                continue
            job = self._repository.get(publication.job_id)
            context = self._repository.get_job_learning_context(job.id)
            content = self._repository.get_platform_content(publication.platform_content_id)
            if (
                job.workspace_id != workspace_id
                or content.format != cohort.format
                or context is None
                or context.mode is not self.policy.mode
                or context.topic_category != cohort.topic_category
                or context.objective != cohort.objective
            ):
                continue
            receipts = self._repository.list_publication_receipts(publication.id)
            if not receipts or max(item.published_at for item in receipts) < oldest:
                continue
            eligible += 1
            selected = []
            exact = True
            for receipt in receipts:
                snapshot, is_exact = self._select_snapshot(receipt.id, receipt.published_at, cohort)
                if snapshot is None:
                    selected = []
                    break
                selected.append(snapshot)
                exact = exact and is_exact
            if selected:
                samples.append(
                    _CohortSample(
                        publication_id=publication.id,
                        receipt_ids=tuple(item.id for item in receipts),
                        snapshot_ids=tuple(item.id for item in selected),
                        value=sum((item.value for item in selected), Decimal(0)),
                        exact_window=exact,
                    )
                )
        return tuple(samples), eligible

    def _select_snapshot(self, receipt_id: str, published_at: datetime, cohort: CohortDefinition):
        snapshots = [
            item
            for item in self._repository.list_metric_snapshots(
                receipt_id=receipt_id, metric_key=cohort.metric_key
            )
            if (
                item.source.startswith("demo.analytics.")
                if self.policy.mode is LearningMode.DEMO
                else not item.source.startswith("demo.analytics.")
            )
        ]
        if not snapshots:
            return None, False
        if self.policy.mode is LearningMode.DEMO:
            return max(snapshots, key=lambda item: (item.observed_at, item.collected_at)), False
        target = published_at + timedelta(hours=cohort.window_hours)
        selected = min(snapshots, key=lambda item: abs(item.observed_at - target))
        exact = abs(selected.observed_at - target) <= timedelta(
            hours=self.policy.window_tolerance_hours
        )
        return (selected, True) if exact else (None, False)

    def _propose(
        self,
        observation: PerformanceObservation,
        cohort_a: CohortDefinition,
        cohort_b: CohortDefinition,
        *,
        actor: str,
    ) -> OptimizationRecommendation:
        if abs(observation.relative_difference_percent) >= self.policy.minimum_effect_percent:
            preferred = (
                cohort_b.format if observation.median_b > observation.median_a else cohort_a.format
            )
            kind = RecommendationKind.TEST_FORMAT
            parameters: Mapping[str, Any] = {
                "preferred_format": preferred,
                "comparison_metric": observation.metric_key,
                "window_hours": observation.window_hours,
            }
            rationale = (
                f"Tester le format {preferred} dans ce périmètre : la médiane observée "
                f"diffère de {abs(observation.relative_difference_percent):.1f} % à "
                f"{observation.window_hours} h. Cette corrélation ne prouve pas une causalité."
            )
        else:
            kind = RecommendationKind.PRESERVE_CURRENT_APPROACH
            parameters = {
                "comparison_metric": observation.metric_key,
                "window_hours": observation.window_hours,
            }
            rationale = (
                "Conserver l’approche actuelle : l’écart médian observé reste sous le seuil "
                "configuré et ne justifie pas une préférence de format."
            )
        now = self._clock()
        recommendation = self._repository.add_optimization_recommendation(
            OptimizationRecommendation(
                id=self._id_factory(),
                observation_id=observation.id,
                workspace_id=observation.workspace_id,
                mode=observation.mode,
                platform=observation.platform,
                topic_category=cohort_a.topic_category,
                objective=cohort_a.objective,
                kind=kind,
                parameters=parameters,
                rationale=rationale,
                evidence_strength=observation.evidence_strength,
                status=RecommendationStatus.PROPOSED,
                created_at=now,
                expires_at=now + timedelta(days=self.policy.recommendation_ttl_days),
            )
        )
        for existing in self._repository.list_optimization_recommendations(
            workspace_id=recommendation.workspace_id, status=RecommendationStatus.ACCEPTED
        ):
            if self._same_scope(existing, recommendation) and dict(existing.parameters) != dict(
                recommendation.parameters
            ):
                self._repository.save_optimization_recommendation(
                    replace(existing, potentially_outdated=True)
                )
        self._event(
            "learning_recommendation_proposed",
            "recommendation",
            recommendation.id,
            actor,
            {
                "kind": recommendation.kind.value,
                "evidence_strength": recommendation.evidence_strength.value,
            },
        )
        return recommendation

    @staticmethod
    def _same_scope(a: OptimizationRecommendation, b: OptimizationRecommendation) -> bool:
        return (
            a.workspace_id,
            a.mode,
            a.platform,
            a.topic_category,
            a.objective,
        ) == (
            b.workspace_id,
            b.mode,
            b.platform,
            b.topic_category,
            b.objective,
        )

    def _expire_recommendation(
        self, recommendation: OptimizationRecommendation, *, now: datetime
    ) -> None:
        expired = replace(
            recommendation,
            status=RecommendationStatus.EXPIRED,
            decided_at=now,
            decided_by="system",
            decision_reason="recommendation validity period ended",
        )
        self._repository.save_optimization_recommendation(expired)
        for entry in self._repository.list_learning_profile_entries(
            recommendation_id=recommendation.id
        ):
            self._repository.save_learning_profile_entry(replace(entry, active=False))
        self._event("learning_recommendation_expired", "recommendation", expired.id, "system", {})

    def _event(
        self,
        event: str,
        entity_type: str,
        entity_id: str,
        actor: str,
        metadata: Mapping[str, Any],
    ) -> None:
        self._repository.add_learning_event(
            LearningAuditEvent(
                id=self._id_factory(),
                event=event,
                entity_type=entity_type,
                entity_id=entity_id,
                actor=actor,
                metadata=metadata,
                created_at=self._clock(),
            )
        )
