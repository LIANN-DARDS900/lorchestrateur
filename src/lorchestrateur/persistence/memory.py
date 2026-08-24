"""Thread-safe in-memory repository for unit tests and local composition."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from threading import RLock

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
    PublicationStatus,
)
from lorchestrateur.domain.workflow import ContentJob, JobStep
from lorchestrateur.persistence.contracts import (
    ArtifactNotFoundError,
    ConcurrentUpdateError,
    DuplicateArtifactError,
    DuplicateJobError,
    JobNotFoundError,
)


class InMemoryContentJobRepository:
    def __init__(self) -> None:
        self._jobs: dict[str, ContentJob] = {}
        self._steps: dict[str, list[JobStep]] = {}
        self._sources: dict[str, SourceEvidence] = {}
        self._strategies: dict[str, ContentStrategy] = {}
        self._master_contents: dict[str, MasterContent] = {}
        self._platform_contents: dict[str, PlatformContentRecord] = {}
        self._publications: dict[str, PublicationRequest] = {}
        self._publication_attempts: dict[str, list[PublicationAttempt]] = {}
        self._publication_receipts: dict[str, list[PublicationReceipt]] = {}
        self._media_assets: dict[str, MediaAsset] = {}
        self._metric_definitions: dict[str, MetricDefinition] = {}
        self._analytics_runs: dict[str, AnalyticsCollectionRun] = {}
        self._metric_snapshots: dict[str, MetricSnapshot] = {}
        self._job_learning_contexts: dict[str, JobLearningContext] = {}
        self._learning_runs: dict[str, LearningAnalysisRun] = {}
        self._performance_observations: dict[str, PerformanceObservation] = {}
        self._optimization_recommendations: dict[str, OptimizationRecommendation] = {}
        self._learning_profiles: dict[str, LearningProfile] = {}
        self._learning_profile_entries: dict[str, LearningProfileEntry] = {}
        self._learning_events: dict[str, LearningAuditEvent] = {}
        self._lock = RLock()

    def add(self, job: ContentJob) -> None:
        with self._lock:
            if job.id in self._jobs:
                raise DuplicateJobError(f"content job already exists: {job.id}")
            self._jobs[job.id] = job
            self._steps[job.id] = []

    def get(self, job_id: str) -> ContentJob:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise JobNotFoundError(f"content job not found: {job_id}") from exc

    def list_jobs(self) -> tuple[ContentJob, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._jobs.values(),
                    key=lambda job: (job.created_at, job.id),
                    reverse=True,
                )
            )

    def save(self, job: ContentJob, step: JobStep) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            self._commit_checkpoint(job, step)

    def list_steps(self, job_id: str) -> tuple[JobStep, ...]:
        with self._lock:
            if job_id not in self._jobs:
                raise JobNotFoundError(f"content job not found: {job_id}")
            return tuple(self._steps[job_id])

    def add_source_with_checkpoint(
        self, source: SourceEvidence, job: ContentJob, step: JobStep
    ) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            if source.job_id != job.id:
                raise ValueError("source and checkpoint belong to different jobs")
            if source.id in self._sources:
                raise DuplicateArtifactError(f"source already exists: {source.id}")
            self._sources[source.id] = source
            self._commit_checkpoint(job, step)

    def get_source(self, source_id: str) -> SourceEvidence:
        with self._lock:
            try:
                return self._sources[source_id]
            except KeyError as exc:
                raise ArtifactNotFoundError(f"source not found: {source_id}") from exc

    def list_sources(self, job_id: str) -> tuple[SourceEvidence, ...]:
        with self._lock:
            self.get(job_id)
            return tuple(source for source in self._sources.values() if source.job_id == job_id)

    def save_strategy_with_checkpoint(
        self, strategy: ContentStrategy, job: ContentJob, step: JobStep
    ) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            if strategy.job_id != job.id:
                raise ValueError("strategy and checkpoint belong to different jobs")
            if strategy.job_id in self._strategies:
                raise DuplicateArtifactError(
                    f"content strategy already exists for job: {strategy.job_id}"
                )
            self._strategies[strategy.job_id] = strategy
            self._commit_checkpoint(job, step)

    def get_strategy(self, job_id: str) -> ContentStrategy:
        with self._lock:
            try:
                return self._strategies[job_id]
            except KeyError as exc:
                raise ArtifactNotFoundError(
                    f"content strategy not found for job: {job_id}"
                ) from exc

    def save_master_content_with_checkpoint(
        self, master_content: MasterContent, job: ContentJob, step: JobStep
    ) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            if master_content.job_id != job.id:
                raise ValueError("master content and checkpoint belong to different jobs")
            if master_content.job_id in self._master_contents:
                raise DuplicateArtifactError(
                    f"master content already exists for job: {master_content.job_id}"
                )
            self._master_contents[master_content.job_id] = master_content
            self._commit_checkpoint(job, step)

    def get_master_content(self, job_id: str) -> MasterContent:
        with self._lock:
            try:
                return self._master_contents[job_id]
            except KeyError as exc:
                raise ArtifactNotFoundError(f"master content not found for job: {job_id}") from exc

    def save_platform_content_with_checkpoint(
        self, content: PlatformContentRecord, job: ContentJob, step: JobStep
    ) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            if content.job_id != job.id:
                raise ValueError("platform content and checkpoint belong to different jobs")
            if content.id in self._platform_contents:
                raise DuplicateArtifactError(f"platform content already exists: {content.id}")
            for existing in self._platform_contents.values():
                same_lineage = (
                    existing.job_id == content.job_id
                    and existing.master_content_id == content.master_content_id
                    and existing.platform == content.platform
                )
                if same_lineage and existing.revision == content.revision:
                    raise DuplicateArtifactError(
                        "platform content revision already exists for this job and platform"
                    )
                if same_lineage and existing.generation_attempt_id == content.generation_attempt_id:
                    raise DuplicateArtifactError(
                        "platform content already exists for this generation attempt"
                    )
            self._platform_contents[content.id] = content
            self._commit_checkpoint(job, step)

    def get_platform_content(self, content_id: str) -> PlatformContentRecord:
        with self._lock:
            try:
                return self._platform_contents[content_id]
            except KeyError as exc:
                raise ArtifactNotFoundError(f"platform content not found: {content_id}") from exc

    def list_platform_contents(
        self, job_id: str, *, platform: str | None = None
    ) -> tuple[PlatformContentRecord, ...]:
        with self._lock:
            self.get(job_id)
            normalized_platform = platform.strip().lower() if platform else None
            return tuple(
                sorted(
                    (
                        content
                        for content in self._platform_contents.values()
                        if content.job_id == job_id
                        and (normalized_platform is None or content.platform == normalized_platform)
                    ),
                    key=lambda content: (content.platform, content.revision, content.id),
                )
            )

    def get_platform_content_by_attempt(
        self,
        job_id: str,
        master_content_id: str,
        platform: str,
        generation_attempt_id: str,
    ) -> PlatformContentRecord | None:
        normalized_platform = platform.strip().lower()
        with self._lock:
            for content in self._platform_contents.values():
                if (
                    content.job_id == job_id
                    and content.master_content_id == master_content_id
                    and content.platform == normalized_platform
                    and content.generation_attempt_id == generation_attempt_id
                ):
                    return content
        return None

    def save_platform_evaluations_with_checkpoint(
        self,
        contents: tuple[PlatformContentRecord, ...],
        job: ContentJob,
        step: JobStep,
    ) -> None:
        with self._lock:
            self._validate_checkpoint(job, step)
            for content in contents:
                existing = self._platform_contents.get(content.id)
                if existing is None:
                    raise ArtifactNotFoundError(f"platform content not found: {content.id}")
                immutable_identity = (
                    existing.job_id,
                    existing.master_content_id,
                    existing.platform,
                    existing.revision,
                    existing.generation_attempt_id,
                )
                next_identity = (
                    content.job_id,
                    content.master_content_id,
                    content.platform,
                    content.revision,
                    content.generation_attempt_id,
                )
                if immutable_identity != next_identity:
                    raise ValueError("platform evaluation cannot change artifact identity")
            for content in contents:
                self._platform_contents[content.id] = content
            self._commit_checkpoint(job, step)

    def add_publication(self, publication: PublicationRequest) -> PublicationRequest:
        with self._lock:
            self.get(publication.job_id)
            if publication.id in self._publications:
                raise DuplicateArtifactError(f"publication already exists: {publication.id}")
            existing = self.get_publication_by_idempotency_key(publication.idempotency_key)
            if existing is not None:
                return existing
            self._publications[publication.id] = publication
            self._publication_attempts[publication.id] = []
            self._publication_receipts[publication.id] = []
            return publication

    def get_publication(self, publication_id: str) -> PublicationRequest:
        with self._lock:
            try:
                return self._publications[publication_id]
            except KeyError as exc:
                raise ArtifactNotFoundError(f"publication not found: {publication_id}") from exc

    def get_publication_by_idempotency_key(self, idempotency_key: str) -> PublicationRequest | None:
        with self._lock:
            return next(
                (
                    publication
                    for publication in self._publications.values()
                    if publication.idempotency_key == idempotency_key
                ),
                None,
            )

    def list_publications(self, job_id: str | None = None) -> tuple[PublicationRequest, ...]:
        with self._lock:
            values = (
                publication
                for publication in self._publications.values()
                if job_id is None or publication.job_id == job_id
            )
            return tuple(sorted(values, key=lambda item: (item.created_at, item.id)))

    def save_publication(self, publication: PublicationRequest) -> None:
        with self._lock:
            if publication.id not in self._publications:
                raise ArtifactNotFoundError(f"publication not found: {publication.id}")
            self._publications[publication.id] = publication

    def add_publication_attempt(self, attempt: PublicationAttempt) -> None:
        with self._lock:
            if attempt.publication_id not in self._publications:
                raise ArtifactNotFoundError(f"publication not found: {attempt.publication_id}")
            attempts = self._publication_attempts[attempt.publication_id]
            if any(
                item.id == attempt.id or item.attempt_number == attempt.attempt_number
                for item in attempts
            ):
                raise DuplicateArtifactError("publication attempt already exists")
            attempts.append(attempt)

    def list_publication_attempts(self, publication_id: str) -> tuple[PublicationAttempt, ...]:
        with self._lock:
            self.get_publication(publication_id)
            return tuple(
                sorted(
                    self._publication_attempts[publication_id],
                    key=lambda item: item.attempt_number,
                )
            )

    def add_publication_receipt(self, receipt: PublicationReceipt) -> None:
        with self._lock:
            if receipt.publication_id not in self._publications:
                raise ArtifactNotFoundError(f"publication not found: {receipt.publication_id}")
            receipts = self._publication_receipts[receipt.publication_id]
            if any(
                item.id == receipt.id or item.item_index == receipt.item_index for item in receipts
            ):
                raise DuplicateArtifactError("publication receipt already exists")
            receipts.append(receipt)

    def list_publication_receipts(self, publication_id: str) -> tuple[PublicationReceipt, ...]:
        with self._lock:
            self.get_publication(publication_id)
            return tuple(
                sorted(
                    self._publication_receipts[publication_id],
                    key=lambda item: item.item_index,
                )
            )

    def get_publication_receipt(self, receipt_id: str) -> PublicationReceipt:
        with self._lock:
            for receipts in self._publication_receipts.values():
                for receipt in receipts:
                    if receipt.id == receipt_id:
                        return receipt
        raise ArtifactNotFoundError(f"publication receipt not found: {receipt_id}")

    def add_media_asset(self, asset: MediaAsset) -> None:
        with self._lock:
            self.get(asset.job_id)
            if asset.id in self._media_assets:
                raise DuplicateArtifactError(f"media asset already exists: {asset.id}")
            if any(
                item.platform_content_id == asset.platform_content_id and item.order == asset.order
                for item in self._media_assets.values()
            ):
                raise DuplicateArtifactError("media asset order already exists")
            self._media_assets[asset.id] = asset

    def list_media_assets(self, platform_content_id: str) -> tuple[MediaAsset, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._media_assets.values()
                        if item.platform_content_id == platform_content_id
                    ),
                    key=lambda item: (item.order, item.id),
                )
            )

    def claim_due_publications(
        self,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[PublicationRequest, ...]:
        with self._lock:
            eligible = [
                publication
                for publication in self._publications.values()
                if publication.status in {PublicationStatus.READY, PublicationStatus.SCHEDULED}
                and (
                    publication.status is PublicationStatus.READY
                    or publication.scheduled_at is not None
                    and publication.scheduled_at <= now
                )
                and (
                    publication.claim_owner is None
                    or publication.lease_expires_at is not None
                    and publication.lease_expires_at <= now
                )
            ]
            claimed = []
            for publication in sorted(
                eligible,
                key=lambda item: (item.scheduled_at or item.created_at, item.id),
            )[:limit]:
                next_status = (
                    PublicationStatus.READY
                    if publication.status is PublicationStatus.SCHEDULED
                    else publication.status
                )
                updated = replace(
                    publication,
                    status=next_status,
                    claim_owner=owner,
                    claimed_at=now,
                    lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
                self._publications[updated.id] = updated
                claimed.append(updated)
            return tuple(claimed)

    def claim_publication(
        self,
        publication_id: str,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> PublicationRequest | None:
        with self._lock:
            publication = self.get_publication(publication_id)
            due = publication.status is PublicationStatus.READY or (
                publication.status is PublicationStatus.SCHEDULED
                and publication.scheduled_at is not None
                and publication.scheduled_at <= now
            )
            claimable = publication.claim_owner is None or (
                publication.lease_expires_at is not None and publication.lease_expires_at <= now
            )
            if not due or not claimable:
                return None
            updated = replace(
                publication,
                status=PublicationStatus.READY,
                claim_owner=owner,
                claimed_at=now,
                lease_expires_at=lease_expires_at,
                updated_at=now,
            )
            self._publications[publication_id] = updated
            return updated

    def recover_expired_publications(self, *, now: datetime) -> tuple[PublicationRequest, ...]:
        with self._lock:
            recovered = []
            for publication in tuple(self._publications.values()):
                if (
                    publication.status is PublicationStatus.PUBLISHING
                    and publication.lease_expires_at is not None
                    and publication.lease_expires_at <= now
                ):
                    updated = replace(
                        publication,
                        status=PublicationStatus.NEEDS_RECONCILIATION,
                        claim_owner=None,
                        claimed_at=None,
                        lease_expires_at=None,
                        updated_at=now,
                    )
                    self._publications[updated.id] = updated
                    recovered.append(updated)
            return tuple(recovered)

    def upsert_metric_definition(self, definition: MetricDefinition) -> None:
        with self._lock:
            self._metric_definitions[definition.key] = definition

    def list_metric_definitions(
        self, *, platform: str | None = None
    ) -> tuple[MetricDefinition, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._metric_definitions.values()
                        if platform is None or item.platform == platform
                    ),
                    key=lambda item: item.key,
                )
            )

    def add_analytics_run(self, run: AnalyticsCollectionRun) -> AnalyticsCollectionRun:
        with self._lock:
            existing = self.get_analytics_run_by_idempotency_key(run.idempotency_key)
            if existing is not None:
                return existing
            if run.id in self._analytics_runs:
                raise DuplicateArtifactError(f"analytics run already exists: {run.id}")
            self._analytics_runs[run.id] = run
            return run

    def get_analytics_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> AnalyticsCollectionRun | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._analytics_runs.values()
                    if item.idempotency_key == idempotency_key
                ),
                None,
            )

    def save_analytics_run(self, run: AnalyticsCollectionRun) -> None:
        with self._lock:
            if run.id not in self._analytics_runs:
                raise ArtifactNotFoundError(f"analytics run not found: {run.id}")
            self._analytics_runs[run.id] = run

    def list_analytics_runs(
        self,
        *,
        receipt_id: str | None = None,
        job_id: str | None = None,
    ) -> tuple[AnalyticsCollectionRun, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._analytics_runs.values()
                        if (receipt_id is None or item.publication_receipt_id == receipt_id)
                        and (job_id is None or item.job_id == job_id)
                    ),
                    key=lambda item: (item.started_at, item.id),
                )
            )

    def add_metric_snapshot(self, snapshot: MetricSnapshot) -> MetricSnapshot:
        with self._lock:
            existing = next(
                (
                    item
                    for item in self._metric_snapshots.values()
                    if item.collection_run_id == snapshot.collection_run_id
                    and item.metric_key == snapshot.metric_key
                ),
                None,
            )
            if existing is not None:
                return existing
            if snapshot.id in self._metric_snapshots:
                raise DuplicateArtifactError(f"metric snapshot already exists: {snapshot.id}")
            self._metric_snapshots[snapshot.id] = snapshot
            return snapshot

    def list_metric_snapshots(
        self,
        *,
        receipt_id: str | None = None,
        job_id: str | None = None,
        platform: str | None = None,
        metric_key: str | None = None,
    ) -> tuple[MetricSnapshot, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._metric_snapshots.values()
                        if (receipt_id is None or item.publication_receipt_id == receipt_id)
                        and (job_id is None or item.job_id == job_id)
                        and (platform is None or item.platform == platform)
                        and (metric_key is None or item.metric_key == metric_key)
                    ),
                    key=lambda item: (item.observed_at, item.collected_at, item.id),
                )
            )

    def prune_metric_snapshots(self, *, collected_before: datetime) -> int:
        with self._lock:
            removable = [
                item.id
                for item in self._metric_snapshots.values()
                if item.collected_at < collected_before
            ]
            for snapshot_id in removable:
                del self._metric_snapshots[snapshot_id]
            return len(removable)

    def save_job_learning_context(self, context: JobLearningContext) -> None:
        with self._lock:
            self.get(context.job_id)
            self._job_learning_contexts[context.job_id] = context

    def get_job_learning_context(self, job_id: str) -> JobLearningContext | None:
        with self._lock:
            self.get(job_id)
            return self._job_learning_contexts.get(job_id)

    def add_learning_run(self, run: LearningAnalysisRun) -> LearningAnalysisRun:
        with self._lock:
            existing = self.get_learning_run_by_idempotency_key(run.idempotency_key)
            if existing is not None:
                return existing
            if run.id in self._learning_runs:
                raise DuplicateArtifactError(f"learning run already exists: {run.id}")
            self._learning_runs[run.id] = run
            return run

    def get_learning_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> LearningAnalysisRun | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._learning_runs.values()
                    if item.idempotency_key == idempotency_key
                ),
                None,
            )

    def list_learning_runs(self) -> tuple[LearningAnalysisRun, ...]:
        with self._lock:
            return tuple(
                sorted(self._learning_runs.values(), key=lambda item: (item.started_at, item.id))
            )

    def add_performance_observation(
        self, observation: PerformanceObservation
    ) -> PerformanceObservation:
        with self._lock:
            existing = self.get_observation_for_run(observation.analysis_run_id)
            if existing is not None:
                return existing
            if observation.id in self._performance_observations:
                raise DuplicateArtifactError(f"observation already exists: {observation.id}")
            self._performance_observations[observation.id] = observation
            return observation

    def get_observation_for_run(self, run_id: str) -> PerformanceObservation | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._performance_observations.values()
                    if item.analysis_run_id == run_id
                ),
                None,
            )

    def list_performance_observations(self) -> tuple[PerformanceObservation, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._performance_observations.values(),
                    key=lambda item: (item.created_at, item.id),
                    reverse=True,
                )
            )

    def add_optimization_recommendation(
        self, recommendation: OptimizationRecommendation
    ) -> OptimizationRecommendation:
        with self._lock:
            existing = next(
                (
                    item
                    for item in self._optimization_recommendations.values()
                    if item.observation_id == recommendation.observation_id
                ),
                None,
            )
            if existing is not None:
                return existing
            if recommendation.id in self._optimization_recommendations:
                raise DuplicateArtifactError(f"recommendation already exists: {recommendation.id}")
            self._optimization_recommendations[recommendation.id] = recommendation
            return recommendation

    def save_optimization_recommendation(self, recommendation: OptimizationRecommendation) -> None:
        with self._lock:
            if recommendation.id not in self._optimization_recommendations:
                raise ArtifactNotFoundError(f"recommendation not found: {recommendation.id}")
            self._optimization_recommendations[recommendation.id] = recommendation

    def get_optimization_recommendation(self, recommendation_id: str) -> OptimizationRecommendation:
        with self._lock:
            try:
                return self._optimization_recommendations[recommendation_id]
            except KeyError as exc:
                raise ArtifactNotFoundError(
                    f"recommendation not found: {recommendation_id}"
                ) from exc

    def get_recommendation_for_run(self, run_id: str) -> OptimizationRecommendation | None:
        with self._lock:
            observation = self.get_observation_for_run(run_id)
            if observation is None:
                return None
            return next(
                (
                    item
                    for item in self._optimization_recommendations.values()
                    if item.observation_id == observation.id
                ),
                None,
            )

    def list_optimization_recommendations(
        self,
        *,
        workspace_id: str | None = None,
        status: RecommendationStatus | None = None,
    ) -> tuple[OptimizationRecommendation, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        item
                        for item in self._optimization_recommendations.values()
                        if (workspace_id is None or item.workspace_id == workspace_id)
                        and (status is None or item.status is status)
                    ),
                    key=lambda item: (item.created_at, item.id),
                    reverse=True,
                )
            )

    def add_learning_profile(self, profile: LearningProfile) -> LearningProfile:
        with self._lock:
            existing = self.get_learning_profile(profile.workspace_id, profile.mode)
            if existing is not None:
                return existing
            if profile.id in self._learning_profiles:
                raise DuplicateArtifactError(f"learning profile already exists: {profile.id}")
            self._learning_profiles[profile.id] = profile
            return profile

    def get_learning_profile(self, workspace_id: str, mode: LearningMode) -> LearningProfile | None:
        with self._lock:
            return next(
                (
                    item
                    for item in self._learning_profiles.values()
                    if item.workspace_id == workspace_id and item.mode is mode
                ),
                None,
            )

    def add_learning_profile_entry(self, entry: LearningProfileEntry) -> LearningProfileEntry:
        with self._lock:
            if entry.profile_id not in self._learning_profiles:
                raise ArtifactNotFoundError(f"learning profile not found: {entry.profile_id}")
            existing = next(
                (
                    item
                    for item in self._learning_profile_entries.values()
                    if item.recommendation_id == entry.recommendation_id
                ),
                None,
            )
            if existing is not None:
                return existing
            self._learning_profile_entries[entry.id] = entry
            return entry

    def save_learning_profile_entry(self, entry: LearningProfileEntry) -> None:
        with self._lock:
            if entry.id not in self._learning_profile_entries:
                raise ArtifactNotFoundError(f"learning profile entry not found: {entry.id}")
            self._learning_profile_entries[entry.id] = entry

    def list_learning_profile_entries(
        self,
        *,
        workspace_id: str | None = None,
        recommendation_id: str | None = None,
        active_only: bool = False,
    ) -> tuple[LearningProfileEntry, ...]:
        with self._lock:
            profile_ids = {
                item.id
                for item in self._learning_profiles.values()
                if workspace_id is None or item.workspace_id == workspace_id
            }
            return tuple(
                sorted(
                    (
                        item
                        for item in self._learning_profile_entries.values()
                        if item.profile_id in profile_ids
                        and (
                            recommendation_id is None or item.recommendation_id == recommendation_id
                        )
                        and (not active_only or item.active)
                    ),
                    key=lambda item: (item.accepted_at, item.id),
                    reverse=True,
                )
            )

    def add_learning_event(self, event: LearningAuditEvent) -> None:
        with self._lock:
            if event.id in self._learning_events:
                raise DuplicateArtifactError(f"learning event already exists: {event.id}")
            self._learning_events[event.id] = event

    def list_learning_events(self) -> tuple[LearningAuditEvent, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._learning_events.values(),
                    key=lambda item: (item.created_at, item.id),
                )
            )

    def _validate_checkpoint(self, job: ContentJob, step: JobStep) -> None:
        current = self.get(job.id)
        if current.version != job.version - 1:
            raise ConcurrentUpdateError(
                f"stale content job version: expected {current.version + 1}, got {job.version}"
            )
        if step.job_id != job.id or step.sequence != job.version:
            raise ValueError("job step does not match the content job checkpoint")

    def _commit_checkpoint(self, job: ContentJob, step: JobStep) -> None:
        self._jobs[job.id] = job
        self._steps[job.id].append(step)
