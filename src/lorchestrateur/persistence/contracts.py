"""Persistence interface consumed by application services."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from lorchestrateur.domain.analytics import (
    AnalyticsCollectionRun,
    MetricDefinition,
    MetricSnapshot,
)
from lorchestrateur.domain.content import ContentStrategy, MasterContent, SourceEvidence
from lorchestrateur.domain.learning import (
    JobLearningContext,
    LearningAnalysisRun,
    LearningAuditEvent,
    LearningMode,
    LearningProfile,
    LearningProfileEntry,
    OptimizationRecommendation,
    PerformanceObservation,
    RecommendationStatus,
)
from lorchestrateur.domain.platform_content import PlatformContentRecord
from lorchestrateur.domain.publication import (
    MediaAsset,
    PublicationAttempt,
    PublicationReceipt,
    PublicationRequest,
)
from lorchestrateur.domain.workflow import ContentJob, JobStep
from lorchestrateur.domain.workspace import WorkspaceKnowledgeItem, WorkspaceProfile


class JobNotFoundError(LookupError):
    pass


class DuplicateJobError(ValueError):
    pass


class ConcurrentUpdateError(RuntimeError):
    pass


class ArtifactNotFoundError(LookupError):
    pass


class DuplicateArtifactError(ValueError):
    pass


class ContentJobRepository(Protocol):
    def add(self, job: ContentJob) -> None: ...

    def get(self, job_id: str) -> ContentJob: ...

    def list_jobs(self) -> tuple[ContentJob, ...]: ...

    def save(self, job: ContentJob, step: JobStep) -> None: ...

    def list_steps(self, job_id: str) -> tuple[JobStep, ...]: ...


class ContentIntelligenceRepository(ContentJobRepository, Protocol):
    def add_source_with_checkpoint(
        self, source: SourceEvidence, job: ContentJob, step: JobStep
    ) -> None: ...

    def get_source(self, source_id: str) -> SourceEvidence: ...

    def list_sources(self, job_id: str) -> tuple[SourceEvidence, ...]: ...

    def save_strategy_with_checkpoint(
        self, strategy: ContentStrategy, job: ContentJob, step: JobStep
    ) -> None: ...

    def get_strategy(self, job_id: str) -> ContentStrategy: ...

    def save_master_content_with_checkpoint(
        self, master_content: MasterContent, job: ContentJob, step: JobStep
    ) -> None: ...

    def get_master_content(self, job_id: str) -> MasterContent: ...

    def save_platform_content_with_checkpoint(
        self, content: PlatformContentRecord, job: ContentJob, step: JobStep
    ) -> None: ...

    def get_platform_content(self, content_id: str) -> PlatformContentRecord: ...

    def list_platform_contents(
        self, job_id: str, *, platform: str | None = None
    ) -> tuple[PlatformContentRecord, ...]: ...

    def get_platform_content_by_attempt(
        self,
        job_id: str,
        master_content_id: str,
        platform: str,
        generation_attempt_id: str,
    ) -> PlatformContentRecord | None: ...

    def save_platform_evaluations_with_checkpoint(
        self,
        contents: tuple[PlatformContentRecord, ...],
        job: ContentJob,
        step: JobStep,
    ) -> None: ...


class PublicationRepository(ContentIntelligenceRepository, Protocol):
    def add_publication(self, publication: PublicationRequest) -> PublicationRequest: ...

    def get_publication(self, publication_id: str) -> PublicationRequest: ...

    def get_publication_by_idempotency_key(
        self, idempotency_key: str
    ) -> PublicationRequest | None: ...

    def list_publications(self, job_id: str | None = None) -> tuple[PublicationRequest, ...]: ...

    def save_publication(self, publication: PublicationRequest) -> None: ...

    def add_publication_attempt(self, attempt: PublicationAttempt) -> None: ...

    def list_publication_attempts(self, publication_id: str) -> tuple[PublicationAttempt, ...]: ...

    def add_publication_receipt(self, receipt: PublicationReceipt) -> None: ...

    def get_publication_receipt(self, receipt_id: str) -> PublicationReceipt: ...

    def list_publication_receipts(self, publication_id: str) -> tuple[PublicationReceipt, ...]: ...

    def add_media_asset(self, asset: MediaAsset) -> None: ...

    def list_media_assets(self, platform_content_id: str) -> tuple[MediaAsset, ...]: ...

    def claim_due_publications(
        self,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[PublicationRequest, ...]: ...

    def claim_publication(
        self,
        publication_id: str,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> PublicationRequest | None: ...

    def recover_expired_publications(self, *, now: datetime) -> tuple[PublicationRequest, ...]: ...


class AnalyticsRepository(PublicationRepository, Protocol):
    def upsert_metric_definition(self, definition: MetricDefinition) -> None: ...

    def list_metric_definitions(
        self, *, platform: str | None = None
    ) -> tuple[MetricDefinition, ...]: ...

    def add_analytics_run(self, run: AnalyticsCollectionRun) -> AnalyticsCollectionRun: ...

    def get_analytics_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> AnalyticsCollectionRun | None: ...

    def save_analytics_run(self, run: AnalyticsCollectionRun) -> None: ...

    def list_analytics_runs(
        self,
        *,
        receipt_id: str | None = None,
        job_id: str | None = None,
    ) -> tuple[AnalyticsCollectionRun, ...]: ...

    def add_metric_snapshot(self, snapshot: MetricSnapshot) -> MetricSnapshot: ...

    def list_metric_snapshots(
        self,
        *,
        receipt_id: str | None = None,
        job_id: str | None = None,
        platform: str | None = None,
        metric_key: str | None = None,
    ) -> tuple[MetricSnapshot, ...]: ...

    def prune_metric_snapshots(self, *, collected_before: datetime) -> int: ...


class LearningRepository(AnalyticsRepository, Protocol):
    def save_job_learning_context(self, context: JobLearningContext) -> None: ...

    def get_job_learning_context(self, job_id: str) -> JobLearningContext | None: ...

    def add_learning_run(self, run: LearningAnalysisRun) -> LearningAnalysisRun: ...

    def get_learning_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> LearningAnalysisRun | None: ...

    def list_learning_runs(self) -> tuple[LearningAnalysisRun, ...]: ...

    def add_performance_observation(
        self, observation: PerformanceObservation
    ) -> PerformanceObservation: ...

    def get_observation_for_run(self, run_id: str) -> PerformanceObservation | None: ...

    def list_performance_observations(self) -> tuple[PerformanceObservation, ...]: ...

    def add_optimization_recommendation(
        self, recommendation: OptimizationRecommendation
    ) -> OptimizationRecommendation: ...

    def save_optimization_recommendation(
        self, recommendation: OptimizationRecommendation
    ) -> None: ...

    def get_optimization_recommendation(
        self, recommendation_id: str
    ) -> OptimizationRecommendation: ...

    def get_recommendation_for_run(self, run_id: str) -> OptimizationRecommendation | None: ...

    def list_optimization_recommendations(
        self,
        *,
        workspace_id: str | None = None,
        status: RecommendationStatus | None = None,
    ) -> tuple[OptimizationRecommendation, ...]: ...

    def add_learning_profile(self, profile: LearningProfile) -> LearningProfile: ...

    def get_learning_profile(
        self, workspace_id: str, mode: LearningMode
    ) -> LearningProfile | None: ...

    def add_learning_profile_entry(self, entry: LearningProfileEntry) -> LearningProfileEntry: ...

    def save_learning_profile_entry(self, entry: LearningProfileEntry) -> None: ...

    def list_learning_profile_entries(
        self,
        *,
        workspace_id: str | None = None,
        recommendation_id: str | None = None,
        active_only: bool = False,
    ) -> tuple[LearningProfileEntry, ...]: ...

    def add_learning_event(self, event: LearningAuditEvent) -> None: ...

    def list_learning_events(self) -> tuple[LearningAuditEvent, ...]: ...


class AutomationRepository(LearningRepository, Protocol):
    """Persistence needed by the automation-first application layer."""

    def add_workspace_profile(self, profile: WorkspaceProfile) -> WorkspaceProfile: ...

    def save_workspace_profile(self, profile: WorkspaceProfile) -> None: ...

    def get_workspace_profile(self, workspace_id: str) -> WorkspaceProfile: ...

    def get_workspace_profile_by_slug(self, slug: str) -> WorkspaceProfile | None: ...

    def list_workspace_profiles(self) -> tuple[WorkspaceProfile, ...]: ...

    def add_workspace_knowledge(self, item: WorkspaceKnowledgeItem) -> WorkspaceKnowledgeItem: ...

    def save_workspace_knowledge(self, item: WorkspaceKnowledgeItem) -> None: ...

    def get_workspace_knowledge(self, item_id: str) -> WorkspaceKnowledgeItem: ...

    def list_workspace_knowledge(
        self,
        workspace_id: str,
        *,
        reusable_only: bool = False,
        active_only: bool = False,
    ) -> tuple[WorkspaceKnowledgeItem, ...]: ...
