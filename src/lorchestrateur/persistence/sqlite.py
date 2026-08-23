"""SQLite adapter with atomic optimistic job updates and trace checkpoints."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType

from lorchestrateur.domain.analytics import (
    AggregationBehavior,
    AnalyticsCollectionRun,
    AnalyticsRunOutcome,
    MetricDefinition,
    MetricFamily,
    MetricSnapshot,
    MetricUnit,
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
from lorchestrateur.domain.platform_content import (
    PlatformContentRecord,
    PlatformValidationStatus,
    QualityBreakdown,
)
from lorchestrateur.domain.publication import (
    MediaAsset,
    MediaAssetType,
    PublicationAttempt,
    PublicationAttemptOutcome,
    PublicationMode,
    PublicationReceipt,
    PublicationRequest,
    PublicationStatus,
)
from lorchestrateur.domain.validation import ValidationIssue
from lorchestrateur.domain.workflow import ContentJob, ContentJobState, JobStep
from lorchestrateur.persistence.contracts import (
    ArtifactNotFoundError,
    ConcurrentUpdateError,
    DuplicateArtifactError,
    DuplicateJobError,
    JobNotFoundError,
)
from lorchestrateur.platforms.builtins import create_default_registry
from lorchestrateur.platforms.registry import PlatformRegistry


class UnsupportedDatabaseURLError(ValueError):
    pass


class SQLiteContentJobRepository:
    """Local repository; callers depend on the repository protocol, not SQLite APIs."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        initialize: bool = True,
        platform_registry: PlatformRegistry | None = None,
    ) -> None:
        self._database_path = Path(database_path)
        self._platform_registry = platform_registry or create_default_registry()
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        if initialize:
            self.initialize()

    @classmethod
    def from_database_url(
        cls,
        database_url: str,
        *,
        platform_registry: PlatformRegistry | None = None,
    ) -> SQLiteContentJobRepository:
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise UnsupportedDatabaseURLError(
                "the SQLite adapter requires a database URL beginning with sqlite:///"
            )
        path = database_url.removeprefix(prefix)
        if not path:
            raise UnsupportedDatabaseURLError("SQLite database path cannot be empty")
        return cls(path, platform_registry=platform_registry)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._database_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS content_jobs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    idea TEXT NOT NULL,
                    target_platforms TEXT NOT NULL,
                    state TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    repair_attempts INTEGER NOT NULL,
                    paused_from TEXT,
                    status_message TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS job_steps (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES content_jobs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event TEXT NOT NULL,
                    from_state TEXT NOT NULL,
                    to_state TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, sequence)
                );

                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES content_jobs(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    url TEXT,
                    source_type TEXT NOT NULL,
                    relevant_excerpt TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    evidence_status TEXT NOT NULL,
                    source_metadata TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS content_strategies (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE
                        REFERENCES content_jobs(id) ON DELETE CASCADE,
                    objective TEXT NOT NULL,
                    target_audience TEXT NOT NULL,
                    angle TEXT NOT NULL,
                    tone TEXT NOT NULL,
                    key_messages TEXT NOT NULL,
                    intended_outcome TEXT NOT NULL,
                    generation_metadata TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS master_contents (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL UNIQUE
                        REFERENCES content_jobs(id) ON DELETE CASCADE,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    body TEXT NOT NULL,
                    key_points TEXT NOT NULL,
                    source_ids TEXT NOT NULL,
                    generation_metadata TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS platform_contents (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES content_jobs(id) ON DELETE CASCADE,
                    master_content_id TEXT NOT NULL
                        REFERENCES master_contents(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    format TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    generation_metadata TEXT NOT NULL,
                    generation_attempt_id TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    quality_score INTEGER,
                    quality_breakdown TEXT,
                    validation_issues TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(job_id, master_content_id, platform, revision),
                    UNIQUE(job_id, master_content_id, platform, generation_attempt_id)
                );

                CREATE TABLE IF NOT EXISTS publication_requests (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES content_jobs(id) ON DELETE CASCADE,
                    platform_content_id TEXT NOT NULL
                        REFERENCES platform_contents(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    requested_by TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    scheduled_at TEXT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    dry_run INTEGER NOT NULL,
                    claim_owner TEXT,
                    claimed_at TEXT,
                    lease_expires_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS publication_attempts (
                    id TEXT PRIMARY KEY,
                    publication_id TEXT NOT NULL
                        REFERENCES publication_requests(id) ON DELETE CASCADE,
                    attempt_number INTEGER NOT NULL,
                    adapter_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    error_classification TEXT,
                    remote_identifier TEXT,
                    UNIQUE(publication_id, attempt_number)
                );

                CREATE TABLE IF NOT EXISTS publication_receipts (
                    id TEXT PRIMARY KEY,
                    publication_id TEXT NOT NULL
                        REFERENCES publication_requests(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    item_index INTEGER NOT NULL,
                    remote_id TEXT NOT NULL,
                    remote_url TEXT,
                    published_at TEXT NOT NULL,
                    adapter_name TEXT NOT NULL,
                    adapter_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    delivery_kind TEXT NOT NULL,
                    receipt_metadata TEXT NOT NULL,
                    UNIQUE(publication_id, item_index)
                );

                CREATE TABLE IF NOT EXISTS media_assets (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES content_jobs(id) ON DELETE CASCADE,
                    platform_content_id TEXT NOT NULL
                        REFERENCES platform_contents(id) ON DELETE CASCADE,
                    media_type TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    media_order INTEGER NOT NULL,
                    alt_text TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(platform_content_id, media_order)
                );

                CREATE TABLE IF NOT EXISTS metric_definitions (
                    metric_key TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    label TEXT NOT NULL,
                    description TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    family TEXT NOT NULL,
                    aggregation_behavior TEXT NOT NULL,
                    source TEXT NOT NULL,
                    version TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS analytics_collection_runs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    platform TEXT NOT NULL,
                    publication_receipt_id TEXT NOT NULL
                        REFERENCES publication_receipts(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL REFERENCES content_jobs(id) ON DELETE CASCADE,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    outcome TEXT NOT NULL,
                    adapter_name TEXT NOT NULL,
                    adapter_version TEXT NOT NULL,
                    error_classification TEXT,
                    metrics_collected_count INTEGER NOT NULL,
                    unavailable_metric_keys TEXT NOT NULL,
                    retry_count INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS metric_snapshots (
                    id TEXT PRIMARY KEY,
                    collection_run_id TEXT NOT NULL
                        REFERENCES analytics_collection_runs(id) ON DELETE CASCADE,
                    publication_receipt_id TEXT NOT NULL
                        REFERENCES publication_receipts(id) ON DELETE CASCADE,
                    job_id TEXT NOT NULL REFERENCES content_jobs(id) ON DELETE CASCADE,
                    platform_content_id TEXT NOT NULL
                        REFERENCES platform_contents(id) ON DELETE CASCADE,
                    platform TEXT NOT NULL,
                    metric_key TEXT NOT NULL
                        REFERENCES metric_definitions(metric_key),
                    metric_value TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    period_start TEXT,
                    period_end TEXT,
                    source TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    collected_at TEXT NOT NULL,
                    snapshot_metadata TEXT NOT NULL,
                    UNIQUE(collection_run_id, metric_key)
                );

                CREATE INDEX IF NOT EXISTS idx_job_steps_job_id
                    ON job_steps(job_id, sequence);

                CREATE INDEX IF NOT EXISTS idx_sources_job_id
                    ON sources(job_id);

                CREATE INDEX IF NOT EXISTS idx_platform_contents_job_platform
                    ON platform_contents(job_id, platform, revision);

                CREATE INDEX IF NOT EXISTS idx_publications_due
                    ON publication_requests(status, scheduled_at, lease_expires_at);

                CREATE INDEX IF NOT EXISTS idx_publications_job
                    ON publication_requests(job_id, platform, created_at);

                CREATE INDEX IF NOT EXISTS idx_metric_definitions_platform
                    ON metric_definitions(platform, family, metric_key);

                CREATE INDEX IF NOT EXISTS idx_analytics_runs_receipt
                    ON analytics_collection_runs(publication_receipt_id, started_at);

                CREATE INDEX IF NOT EXISTS idx_analytics_runs_job
                    ON analytics_collection_runs(job_id, platform, started_at);

                CREATE INDEX IF NOT EXISTS idx_metric_snapshots_receipt_metric
                    ON metric_snapshots(publication_receipt_id, metric_key, observed_at);

                CREATE INDEX IF NOT EXISTS idx_metric_snapshots_job_platform
                    ON metric_snapshots(job_id, platform, collected_at);

                CREATE INDEX IF NOT EXISTS idx_metric_snapshots_collected
                    ON metric_snapshots(collected_at);

                PRAGMA user_version = 5;
                """
            )

    def add(self, job: ContentJob) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO content_jobs (
                        id, workspace_id, idea, target_platforms, state, version,
                        repair_attempts, paused_from, status_message, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._job_values(job),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateJobError(f"content job already exists: {job.id}") from exc

    def get(self, job_id: str) -> ContentJob:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM content_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise JobNotFoundError(f"content job not found: {job_id}")
        return self._row_to_job(row)

    def list_jobs(self) -> tuple[ContentJob, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM content_jobs ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return tuple(self._row_to_job(row) for row in rows)

    def save(self, job: ContentJob, step: JobStep) -> None:
        with self._connect() as connection:
            self._update_job(connection, job, step)
            self._insert_step(connection, step)

    def list_steps(self, job_id: str) -> tuple[JobStep, ...]:
        self.get(job_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_steps WHERE job_id = ? ORDER BY sequence", (job_id,)
            ).fetchall()
        return tuple(self._row_to_step(row) for row in rows)

    def add_source_with_checkpoint(
        self, source: SourceEvidence, job: ContentJob, step: JobStep
    ) -> None:
        if source.job_id != job.id:
            raise ValueError("source and checkpoint belong to different jobs")
        try:
            with self._connect() as connection:
                self._update_job(connection, job, step)
                connection.execute(
                    """
                    INSERT INTO sources (
                        id, job_id, title, url, source_type, relevant_excerpt,
                        retrieved_at, evidence_status, source_metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        source.id,
                        source.job_id,
                        source.title,
                        source.url,
                        source.source_type.value,
                        source.relevant_excerpt,
                        source.retrieved_at.isoformat(),
                        source.evidence_status.value,
                        json.dumps(dict(source.metadata), sort_keys=True),
                    ),
                )
                self._insert_step(connection, step)
        except sqlite3.IntegrityError as exc:
            raise DuplicateArtifactError(f"source already exists: {source.id}") from exc

    def get_source(self, source_id: str) -> SourceEvidence:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            raise ArtifactNotFoundError(f"source not found: {source_id}")
        return self._row_to_source(row)

    def list_sources(self, job_id: str) -> tuple[SourceEvidence, ...]:
        self.get(job_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM sources WHERE job_id = ? ORDER BY retrieved_at, id",
                (job_id,),
            ).fetchall()
        return tuple(self._row_to_source(row) for row in rows)

    def save_strategy_with_checkpoint(
        self, strategy: ContentStrategy, job: ContentJob, step: JobStep
    ) -> None:
        if strategy.job_id != job.id:
            raise ValueError("strategy and checkpoint belong to different jobs")
        try:
            with self._connect() as connection:
                self._update_job(connection, job, step)
                connection.execute(
                    """
                    INSERT INTO content_strategies (
                        id, job_id, objective, target_audience, angle, tone,
                        key_messages, intended_outcome, generation_metadata,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        strategy.id,
                        strategy.job_id,
                        strategy.objective,
                        strategy.target_audience,
                        strategy.angle,
                        strategy.tone,
                        json.dumps(
                            [
                                {
                                    "message": item.message,
                                    "source_ids": item.source_ids,
                                }
                                for item in strategy.key_messages
                            ],
                            sort_keys=True,
                        ),
                        strategy.intended_outcome,
                        self._generation_metadata_to_json(strategy.generation_metadata),
                        strategy.created_at.isoformat(),
                        strategy.updated_at.isoformat(),
                    ),
                )
                self._insert_step(connection, step)
        except sqlite3.IntegrityError as exc:
            raise DuplicateArtifactError(
                f"content strategy already exists for job: {strategy.job_id}"
            ) from exc

    def get_strategy(self, job_id: str) -> ContentStrategy:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM content_strategies WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(f"content strategy not found for job: {job_id}")
        return self._row_to_strategy(row)

    def save_master_content_with_checkpoint(
        self, master_content: MasterContent, job: ContentJob, step: JobStep
    ) -> None:
        if master_content.job_id != job.id:
            raise ValueError("master content and checkpoint belong to different jobs")
        try:
            with self._connect() as connection:
                self._update_job(connection, job, step)
                connection.execute(
                    """
                    INSERT INTO master_contents (
                        id, job_id, title, summary, body, key_points, source_ids,
                        generation_metadata, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        master_content.id,
                        master_content.job_id,
                        master_content.title,
                        master_content.summary,
                        master_content.body,
                        json.dumps(master_content.key_points),
                        json.dumps(master_content.source_ids),
                        self._generation_metadata_to_json(master_content.generation_metadata),
                        master_content.created_at.isoformat(),
                        master_content.updated_at.isoformat(),
                    ),
                )
                self._insert_step(connection, step)
        except sqlite3.IntegrityError as exc:
            raise DuplicateArtifactError(
                f"master content already exists for job: {master_content.job_id}"
            ) from exc

    def get_master_content(self, job_id: str) -> MasterContent:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM master_contents WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(f"master content not found for job: {job_id}")
        return self._row_to_master_content(row)

    def save_platform_content_with_checkpoint(
        self, content: PlatformContentRecord, job: ContentJob, step: JobStep
    ) -> None:
        if content.job_id != job.id:
            raise ValueError("platform content and checkpoint belong to different jobs")
        try:
            with self._connect() as connection:
                self._update_job(connection, job, step)
                connection.execute(
                    """
                    INSERT INTO platform_contents (
                        id, job_id, master_content_id, platform, format, schema_version,
                        payload, generation_metadata, generation_attempt_id,
                        validation_status, quality_score, quality_breakdown,
                        validation_issues, revision, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._platform_content_values(content),
                )
                self._insert_step(connection, step)
        except sqlite3.IntegrityError as exc:
            raise DuplicateArtifactError(
                "platform content already exists for this revision or generation attempt"
            ) from exc

    def get_platform_content(self, content_id: str) -> PlatformContentRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM platform_contents WHERE id = ?", (content_id,)
            ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(f"platform content not found: {content_id}")
        return self._row_to_platform_content(row)

    def list_platform_contents(
        self, job_id: str, *, platform: str | None = None
    ) -> tuple[PlatformContentRecord, ...]:
        self.get(job_id)
        with self._connect() as connection:
            if platform is None:
                rows = connection.execute(
                    """
                    SELECT * FROM platform_contents
                    WHERE job_id = ? ORDER BY platform, revision, id
                    """,
                    (job_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM platform_contents
                    WHERE job_id = ? AND platform = ?
                    ORDER BY revision, id
                    """,
                    (job_id, platform.strip().lower()),
                ).fetchall()
        return tuple(self._row_to_platform_content(row) for row in rows)

    def get_platform_content_by_attempt(
        self,
        job_id: str,
        master_content_id: str,
        platform: str,
        generation_attempt_id: str,
    ) -> PlatformContentRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM platform_contents
                WHERE job_id = ? AND master_content_id = ? AND platform = ?
                    AND generation_attempt_id = ?
                """,
                (
                    job_id,
                    master_content_id,
                    platform.strip().lower(),
                    generation_attempt_id,
                ),
            ).fetchone()
        return None if row is None else self._row_to_platform_content(row)

    def save_platform_evaluations_with_checkpoint(
        self,
        contents: tuple[PlatformContentRecord, ...],
        job: ContentJob,
        step: JobStep,
    ) -> None:
        with self._connect() as connection:
            self._update_job(connection, job, step)
            for content in contents:
                cursor = connection.execute(
                    """
                    UPDATE platform_contents SET
                        validation_status = ?, quality_score = ?, quality_breakdown = ?,
                        validation_issues = ?, updated_at = ?
                    WHERE id = ? AND job_id = ? AND master_content_id = ?
                        AND platform = ? AND revision = ?
                    """,
                    (
                        content.validation_status.value,
                        content.quality_score,
                        self._quality_breakdown_to_json(content.quality_breakdown),
                        self._validation_issues_to_json(content.validation_issues),
                        content.updated_at.isoformat(),
                        content.id,
                        content.job_id,
                        content.master_content_id,
                        content.platform,
                        content.revision,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ArtifactNotFoundError(
                        f"platform content not found or identity changed: {content.id}"
                    )
            self._insert_step(connection, step)

    def add_publication(self, publication: PublicationRequest) -> PublicationRequest:
        existing = self.get_publication_by_idempotency_key(publication.idempotency_key)
        if existing is not None:
            return existing
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO publication_requests (
                        id, job_id, platform_content_id, platform, requested_by, mode,
                        scheduled_at, idempotency_key, status, dry_run, claim_owner,
                        claimed_at, lease_expires_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._publication_values(publication),
                )
        except sqlite3.IntegrityError as exc:
            duplicate = self.get_publication_by_idempotency_key(publication.idempotency_key)
            if duplicate is not None:
                return duplicate
            raise DuplicateArtifactError(f"publication already exists: {publication.id}") from exc
        return publication

    def get_publication(self, publication_id: str) -> PublicationRequest:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM publication_requests WHERE id = ?", (publication_id,)
            ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(f"publication not found: {publication_id}")
        return self._row_to_publication(row)

    def get_publication_by_idempotency_key(self, idempotency_key: str) -> PublicationRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM publication_requests WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else self._row_to_publication(row)

    def list_publications(self, job_id: str | None = None) -> tuple[PublicationRequest, ...]:
        with self._connect() as connection:
            if job_id is None:
                rows = connection.execute(
                    "SELECT * FROM publication_requests ORDER BY created_at, id"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM publication_requests
                    WHERE job_id = ? ORDER BY created_at, id
                    """,
                    (job_id,),
                ).fetchall()
        return tuple(self._row_to_publication(row) for row in rows)

    def save_publication(self, publication: PublicationRequest) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE publication_requests SET
                    job_id = ?, platform_content_id = ?, platform = ?, requested_by = ?,
                    mode = ?, scheduled_at = ?, idempotency_key = ?, status = ?,
                    dry_run = ?, claim_owner = ?, claimed_at = ?, lease_expires_at = ?,
                    created_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (*self._publication_values(publication)[1:], publication.id),
            )
            if cursor.rowcount != 1:
                raise ArtifactNotFoundError(f"publication not found: {publication.id}")

    def add_publication_attempt(self, attempt: PublicationAttempt) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO publication_attempts (
                        id, publication_id, attempt_number, adapter_name, started_at,
                        finished_at, outcome, error_classification, remote_identifier
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        attempt.id,
                        attempt.publication_id,
                        attempt.attempt_number,
                        attempt.adapter_name,
                        attempt.started_at.isoformat(),
                        attempt.finished_at.isoformat(),
                        attempt.outcome.value,
                        attempt.error_classification,
                        attempt.remote_identifier,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateArtifactError("publication attempt already exists") from exc

    def list_publication_attempts(self, publication_id: str) -> tuple[PublicationAttempt, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM publication_attempts
                WHERE publication_id = ? ORDER BY attempt_number
                """,
                (publication_id,),
            ).fetchall()
        return tuple(self._row_to_publication_attempt(row) for row in rows)

    def add_publication_receipt(self, receipt: PublicationReceipt) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO publication_receipts (
                        id, publication_id, platform, item_index, remote_id, remote_url,
                        published_at, adapter_name, adapter_version, status,
                        delivery_kind, receipt_metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        receipt.id,
                        receipt.publication_id,
                        receipt.platform,
                        receipt.item_index,
                        receipt.remote_id,
                        receipt.remote_url,
                        receipt.published_at.isoformat(),
                        receipt.adapter_name,
                        receipt.adapter_version,
                        receipt.status,
                        receipt.delivery_kind,
                        json.dumps(dict(receipt.metadata), sort_keys=True),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateArtifactError("publication receipt already exists") from exc

    def list_publication_receipts(self, publication_id: str) -> tuple[PublicationReceipt, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM publication_receipts
                WHERE publication_id = ? ORDER BY item_index
                """,
                (publication_id,),
            ).fetchall()
        return tuple(self._row_to_publication_receipt(row) for row in rows)

    def get_publication_receipt(self, receipt_id: str) -> PublicationReceipt:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM publication_receipts WHERE id = ?", (receipt_id,)
            ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(f"publication receipt not found: {receipt_id}")
        return self._row_to_publication_receipt(row)

    def add_media_asset(self, asset: MediaAsset) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO media_assets (
                        id, job_id, platform_content_id, media_type, source_url,
                        media_order, alt_text, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset.id,
                        asset.job_id,
                        asset.platform_content_id,
                        asset.media_type.value,
                        asset.source_url,
                        asset.order,
                        asset.alt_text,
                        asset.created_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise DuplicateArtifactError("media asset already exists") from exc

    def list_media_assets(self, platform_content_id: str) -> tuple[MediaAsset, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM media_assets
                WHERE platform_content_id = ? ORDER BY media_order, id
                """,
                (platform_content_id,),
            ).fetchall()
        return tuple(self._row_to_media_asset(row) for row in rows)

    def claim_due_publications(
        self,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
        limit: int,
    ) -> tuple[PublicationRequest, ...]:
        if limit < 1:
            return ()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM publication_requests
                WHERE status IN (?, ?)
                  AND (status = ? OR scheduled_at <= ?)
                  AND (claim_owner IS NULL OR lease_expires_at <= ?)
                ORDER BY COALESCE(scheduled_at, created_at), id
                LIMIT ?
                """,
                (
                    PublicationStatus.READY.value,
                    PublicationStatus.SCHEDULED.value,
                    PublicationStatus.READY.value,
                    now.isoformat(),
                    now.isoformat(),
                    limit,
                ),
            ).fetchall()
            claimed: list[PublicationRequest] = []
            for row in rows:
                publication = self._row_to_publication(row)
                status = (
                    PublicationStatus.READY
                    if publication.status is PublicationStatus.SCHEDULED
                    else publication.status
                )
                cursor = connection.execute(
                    """
                    UPDATE publication_requests SET
                        status = ?, claim_owner = ?, claimed_at = ?,
                        lease_expires_at = ?, updated_at = ?
                    WHERE id = ?
                      AND (claim_owner IS NULL OR lease_expires_at <= ?)
                    """,
                    (
                        status.value,
                        owner,
                        now.isoformat(),
                        lease_expires_at.isoformat(),
                        now.isoformat(),
                        publication.id,
                        now.isoformat(),
                    ),
                )
                if cursor.rowcount == 1:
                    claimed.append(
                        PublicationRequest(
                            id=publication.id,
                            job_id=publication.job_id,
                            platform_content_id=publication.platform_content_id,
                            platform=publication.platform,
                            requested_by=publication.requested_by,
                            mode=publication.mode,
                            scheduled_at=publication.scheduled_at,
                            idempotency_key=publication.idempotency_key,
                            status=status,
                            dry_run=publication.dry_run,
                            claim_owner=owner,
                            claimed_at=now,
                            lease_expires_at=lease_expires_at,
                            created_at=publication.created_at,
                            updated_at=now,
                        )
                    )
            return tuple(claimed)

    def claim_publication(
        self,
        publication_id: str,
        *,
        owner: str,
        now: datetime,
        lease_expires_at: datetime,
    ) -> PublicationRequest | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM publication_requests WHERE id = ?",
                (publication_id,),
            ).fetchone()
            if row is None:
                raise ArtifactNotFoundError(f"publication not found: {publication_id}")
            publication = self._row_to_publication(row)
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
            cursor = connection.execute(
                """
                UPDATE publication_requests SET status = ?, claim_owner = ?,
                    claimed_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND (claim_owner IS NULL OR lease_expires_at <= ?)
                """,
                (
                    PublicationStatus.READY.value,
                    owner,
                    now.isoformat(),
                    lease_expires_at.isoformat(),
                    now.isoformat(),
                    publication_id,
                    now.isoformat(),
                ),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_publication(publication_id)

    def recover_expired_publications(self, *, now: datetime) -> tuple[PublicationRequest, ...]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM publication_requests
                WHERE status = ? AND lease_expires_at IS NOT NULL
                    AND lease_expires_at <= ?
                ORDER BY lease_expires_at, id
                """,
                (PublicationStatus.PUBLISHING.value, now.isoformat()),
            ).fetchall()
            recovered = []
            for row in rows:
                cursor = connection.execute(
                    """
                    UPDATE publication_requests SET status = ?, claim_owner = NULL,
                        claimed_at = NULL, lease_expires_at = NULL, updated_at = ?
                    WHERE id = ? AND status = ? AND lease_expires_at <= ?
                    """,
                    (
                        PublicationStatus.NEEDS_RECONCILIATION.value,
                        now.isoformat(),
                        row["id"],
                        PublicationStatus.PUBLISHING.value,
                        now.isoformat(),
                    ),
                )
                if cursor.rowcount == 1:
                    recovered.append(
                        replace(
                            self._row_to_publication(row),
                            status=PublicationStatus.NEEDS_RECONCILIATION,
                            claim_owner=None,
                            claimed_at=None,
                            lease_expires_at=None,
                            updated_at=now,
                        )
                    )
            return tuple(recovered)

    def upsert_metric_definition(self, definition: MetricDefinition) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO metric_definitions (
                    metric_key, platform, label, description, unit, family,
                    aggregation_behavior, source, version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(metric_key) DO UPDATE SET
                    platform = excluded.platform,
                    label = excluded.label,
                    description = excluded.description,
                    unit = excluded.unit,
                    family = excluded.family,
                    aggregation_behavior = excluded.aggregation_behavior,
                    source = excluded.source,
                    version = excluded.version
                """,
                (
                    definition.key,
                    definition.platform,
                    definition.label,
                    definition.description,
                    definition.unit.value,
                    definition.family.value,
                    definition.aggregation.value,
                    definition.source,
                    definition.version,
                ),
            )

    def list_metric_definitions(
        self, *, platform: str | None = None
    ) -> tuple[MetricDefinition, ...]:
        with self._connect() as connection:
            if platform is None:
                rows = connection.execute(
                    "SELECT * FROM metric_definitions ORDER BY metric_key"
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM metric_definitions
                    WHERE platform = ? ORDER BY metric_key
                    """,
                    (platform,),
                ).fetchall()
        return tuple(self._row_to_metric_definition(row) for row in rows)

    def add_analytics_run(self, run: AnalyticsCollectionRun) -> AnalyticsCollectionRun:
        existing = self.get_analytics_run_by_idempotency_key(run.idempotency_key)
        if existing is not None:
            return existing
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO analytics_collection_runs (
                        id, idempotency_key, platform, publication_receipt_id,
                        job_id, started_at, completed_at, outcome, adapter_name,
                        adapter_version, error_classification,
                        metrics_collected_count, unavailable_metric_keys, retry_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    self._analytics_run_values(run),
                )
        except sqlite3.IntegrityError as exc:
            existing = self.get_analytics_run_by_idempotency_key(run.idempotency_key)
            if existing is not None:
                return existing
            raise DuplicateArtifactError(f"analytics run already exists: {run.id}") from exc
        return run

    def get_analytics_run_by_idempotency_key(
        self, idempotency_key: str
    ) -> AnalyticsCollectionRun | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM analytics_collection_runs WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else self._row_to_analytics_run(row)

    def save_analytics_run(self, run: AnalyticsCollectionRun) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE analytics_collection_runs SET
                    idempotency_key = ?, platform = ?, publication_receipt_id = ?,
                    job_id = ?, started_at = ?, completed_at = ?, outcome = ?,
                    adapter_name = ?, adapter_version = ?, error_classification = ?,
                    metrics_collected_count = ?, unavailable_metric_keys = ?, retry_count = ?
                WHERE id = ?
                """,
                (*self._analytics_run_values(run)[1:], run.id),
            )
            if cursor.rowcount != 1:
                raise ArtifactNotFoundError(f"analytics run not found: {run.id}")

    def list_analytics_runs(
        self,
        *,
        receipt_id: str | None = None,
        job_id: str | None = None,
    ) -> tuple[AnalyticsCollectionRun, ...]:
        clauses = []
        values = []
        if receipt_id is not None:
            clauses.append("publication_receipt_id = ?")
            values.append(receipt_id)
        if job_id is not None:
            clauses.append("job_id = ?")
            values.append(job_id)
        query = "SELECT * FROM analytics_collection_runs"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY started_at, id"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return tuple(self._row_to_analytics_run(row) for row in rows)

    def add_metric_snapshot(self, snapshot: MetricSnapshot) -> MetricSnapshot:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO metric_snapshots (
                        id, collection_run_id, publication_receipt_id, job_id,
                        platform_content_id, platform, metric_key, metric_value,
                        observed_at, period_start, period_end, source,
                        source_version, collected_at, snapshot_metadata
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        snapshot.id,
                        snapshot.collection_run_id,
                        snapshot.publication_receipt_id,
                        snapshot.job_id,
                        snapshot.platform_content_id,
                        snapshot.platform,
                        snapshot.metric_key,
                        str(snapshot.value),
                        snapshot.observed_at.isoformat(),
                        snapshot.period_start.isoformat() if snapshot.period_start else None,
                        snapshot.period_end.isoformat() if snapshot.period_end else None,
                        snapshot.source,
                        snapshot.source_version,
                        snapshot.collected_at.isoformat(),
                        json.dumps(dict(snapshot.metadata), sort_keys=True),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT * FROM metric_snapshots
                    WHERE collection_run_id = ? AND metric_key = ?
                    """,
                    (snapshot.collection_run_id, snapshot.metric_key),
                ).fetchone()
            if row is not None:
                return self._row_to_metric_snapshot(row)
            raise DuplicateArtifactError(f"metric snapshot already exists: {snapshot.id}") from exc
        return snapshot

    def list_metric_snapshots(
        self,
        *,
        receipt_id: str | None = None,
        job_id: str | None = None,
        platform: str | None = None,
        metric_key: str | None = None,
    ) -> tuple[MetricSnapshot, ...]:
        filters = {
            "publication_receipt_id": receipt_id,
            "job_id": job_id,
            "platform": platform,
            "metric_key": metric_key,
        }
        clauses = [f"{key} = ?" for key, value in filters.items() if value is not None]
        values = [value for value in filters.values() if value is not None]
        query = "SELECT * FROM metric_snapshots"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY observed_at, collected_at, id"
        with self._connect() as connection:
            rows = connection.execute(query, tuple(values)).fetchall()
        return tuple(self._row_to_metric_snapshot(row) for row in rows)

    def prune_metric_snapshots(self, *, collected_before: datetime) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM metric_snapshots WHERE collected_at < ?",
                (collected_before.isoformat(),),
            )
            return cursor.rowcount

    @staticmethod
    def _update_job(connection: sqlite3.Connection, job: ContentJob, step: JobStep) -> None:
        if step.job_id != job.id or step.sequence != job.version:
            raise ValueError("job step does not match the content job checkpoint")
        expected_version = job.version - 1
        cursor = connection.execute(
            """
            UPDATE content_jobs SET
                workspace_id = ?, idea = ?, target_platforms = ?, state = ?,
                version = ?, repair_attempts = ?, paused_from = ?, status_message = ?,
                created_at = ?, updated_at = ?
            WHERE id = ? AND version = ?
            """,
            (
                job.workspace_id,
                job.idea,
                json.dumps(job.target_platforms),
                job.state.value,
                job.version,
                job.repair_attempts,
                job.paused_from.value if job.paused_from else None,
                job.status_message,
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
                job.id,
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise ConcurrentUpdateError(
                f"content job {job.id} is missing or no longer at version {expected_version}"
            )

    @staticmethod
    def _insert_step(connection: sqlite3.Connection, step: JobStep) -> None:
        connection.execute(
            """
            INSERT INTO job_steps (
                id, job_id, sequence, event, from_state, to_state, details, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                step.id,
                step.job_id,
                step.sequence,
                step.event,
                step.from_state.value,
                step.to_state.value,
                json.dumps(dict(step.details), sort_keys=True),
                step.created_at.isoformat(),
            ),
        )

    @staticmethod
    def _generation_metadata_to_json(
        metadata: GenerationMetadata | None,
    ) -> str | None:
        if metadata is None:
            return None
        return json.dumps(
            {
                "provider": metadata.provider,
                "model": metadata.model,
                "task": metadata.task,
                "generated_at": metadata.generated_at.isoformat(),
                "duration_ms": metadata.duration_ms,
                "requested_at": (
                    metadata.requested_at.isoformat() if metadata.requested_at is not None else None
                ),
                "provider_latency_ms": metadata.provider_latency_ms,
                "retry_count": metadata.retry_count,
                "input_tokens": metadata.input_tokens,
                "output_tokens": metadata.output_tokens,
                "total_tokens": metadata.total_tokens,
                "estimated_cost": metadata.estimated_cost,
                "cost_class": metadata.cost_class,
            },
            sort_keys=True,
        )

    @classmethod
    def _platform_content_values(cls, content: PlatformContentRecord) -> tuple[object, ...]:
        return (
            content.id,
            content.job_id,
            content.master_content_id,
            content.platform,
            content.format,
            content.schema_version,
            json.dumps(dict(content.payload.to_mapping()), sort_keys=True),
            cls._generation_metadata_to_json(content.generation_metadata),
            content.generation_attempt_id,
            content.validation_status.value,
            content.quality_score,
            cls._quality_breakdown_to_json(content.quality_breakdown),
            cls._validation_issues_to_json(content.validation_issues),
            content.revision,
            content.created_at.isoformat(),
            content.updated_at.isoformat(),
        )

    @staticmethod
    def _publication_values(publication: PublicationRequest) -> tuple[object, ...]:
        return (
            publication.id,
            publication.job_id,
            publication.platform_content_id,
            publication.platform,
            publication.requested_by,
            publication.mode.value,
            publication.scheduled_at.isoformat() if publication.scheduled_at else None,
            publication.idempotency_key,
            publication.status.value,
            int(publication.dry_run),
            publication.claim_owner,
            publication.claimed_at.isoformat() if publication.claimed_at else None,
            (publication.lease_expires_at.isoformat() if publication.lease_expires_at else None),
            publication.created_at.isoformat(),
            publication.updated_at.isoformat(),
        )

    @staticmethod
    def _analytics_run_values(run: AnalyticsCollectionRun) -> tuple[object, ...]:
        return (
            run.id,
            run.idempotency_key,
            run.platform,
            run.publication_receipt_id,
            run.job_id,
            run.started_at.isoformat(),
            run.completed_at.isoformat() if run.completed_at else None,
            run.outcome.value,
            run.adapter_name,
            run.adapter_version,
            run.error_classification,
            run.metrics_collected_count,
            json.dumps(run.unavailable_metric_keys),
            run.retry_count,
        )

    @staticmethod
    def _quality_breakdown_to_json(breakdown: QualityBreakdown | None) -> str | None:
        if breakdown is None:
            return None
        return json.dumps(dict(breakdown.to_mapping()), sort_keys=True)

    @staticmethod
    def _validation_issues_to_json(
        issues: tuple[ValidationIssue, ...],
    ) -> str:
        return json.dumps(
            [
                {"code": issue.code, "message": issue.message, "field": issue.field}
                for issue in issues
            ],
            sort_keys=True,
        )

    @staticmethod
    def _job_values(job: ContentJob) -> tuple[object, ...]:
        return (
            job.id,
            job.workspace_id,
            job.idea,
            json.dumps(job.target_platforms),
            job.state.value,
            job.version,
            job.repair_attempts,
            job.paused_from.value if job.paused_from else None,
            job.status_message,
            job.created_at.isoformat(),
            job.updated_at.isoformat(),
        )

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> ContentJob:
        return ContentJob(
            id=row["id"],
            workspace_id=row["workspace_id"],
            idea=row["idea"],
            target_platforms=tuple(json.loads(row["target_platforms"])),
            state=ContentJobState(row["state"]),
            version=row["version"],
            repair_attempts=row["repair_attempts"],
            paused_from=(ContentJobState(row["paused_from"]) if row["paused_from"] else None),
            status_message=row["status_message"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_step(row: sqlite3.Row) -> JobStep:
        return JobStep(
            id=row["id"],
            job_id=row["job_id"],
            sequence=row["sequence"],
            event=row["event"],
            from_state=ContentJobState(row["from_state"]),
            to_state=ContentJobState(row["to_state"]),
            details=MappingProxyType(json.loads(row["details"])),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_source(row: sqlite3.Row) -> SourceEvidence:
        return SourceEvidence(
            id=row["id"],
            job_id=row["job_id"],
            title=row["title"],
            url=row["url"],
            source_type=SourceType(row["source_type"]),
            relevant_excerpt=row["relevant_excerpt"],
            retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
            evidence_status=EvidenceStatus(row["evidence_status"]),
            metadata=MappingProxyType(json.loads(row["source_metadata"])),
        )

    @classmethod
    def _row_to_strategy(cls, row: sqlite3.Row) -> ContentStrategy:
        key_messages = tuple(
            StrategyKeyMessage(message=item["message"], source_ids=tuple(item["source_ids"]))
            for item in json.loads(row["key_messages"])
        )
        return ContentStrategy(
            id=row["id"],
            job_id=row["job_id"],
            objective=row["objective"],
            target_audience=row["target_audience"],
            angle=row["angle"],
            tone=row["tone"],
            key_messages=key_messages,
            intended_outcome=row["intended_outcome"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            generation_metadata=cls._generation_metadata_from_json(row["generation_metadata"]),
        )

    @classmethod
    def _row_to_master_content(cls, row: sqlite3.Row) -> MasterContent:
        return MasterContent(
            id=row["id"],
            job_id=row["job_id"],
            title=row["title"],
            summary=row["summary"],
            body=row["body"],
            key_points=tuple(json.loads(row["key_points"])),
            source_ids=tuple(json.loads(row["source_ids"])),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            generation_metadata=cls._generation_metadata_from_json(row["generation_metadata"]),
        )

    def _row_to_platform_content(self, row: sqlite3.Row) -> PlatformContentRecord:
        metadata = self._generation_metadata_from_json(row["generation_metadata"])
        if metadata is None:
            raise ValueError("platform content generation metadata is missing")
        raw_breakdown = (
            json.loads(row["quality_breakdown"]) if row["quality_breakdown"] is not None else None
        )
        breakdown = (
            QualityBreakdown(
                structure=raw_breakdown["structure"],
                completeness=raw_breakdown["completeness"],
                platform_fit=raw_breakdown["platform_fit"],
                evidence_integrity=raw_breakdown["evidence_integrity"],
                content_hygiene=raw_breakdown["content_hygiene"],
            )
            if raw_breakdown is not None
            else None
        )
        return PlatformContentRecord(
            id=row["id"],
            job_id=row["job_id"],
            master_content_id=row["master_content_id"],
            platform=row["platform"],
            format=row["format"],
            schema_version=row["schema_version"],
            payload=self._platform_registry.parse_payload(
                row["platform"], json.loads(row["payload"])
            ),
            generation_metadata=metadata,
            generation_attempt_id=row["generation_attempt_id"],
            validation_status=PlatformValidationStatus(row["validation_status"]),
            quality_score=row["quality_score"],
            quality_breakdown=breakdown,
            validation_issues=tuple(
                ValidationIssue(
                    code=item["code"],
                    message=item["message"],
                    field=item.get("field"),
                )
                for item in json.loads(row["validation_issues"])
            ),
            revision=row["revision"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_publication(row: sqlite3.Row) -> PublicationRequest:
        return PublicationRequest(
            id=row["id"],
            job_id=row["job_id"],
            platform_content_id=row["platform_content_id"],
            platform=row["platform"],
            requested_by=row["requested_by"],
            mode=PublicationMode(row["mode"]),
            scheduled_at=(
                datetime.fromisoformat(row["scheduled_at"]) if row["scheduled_at"] else None
            ),
            idempotency_key=row["idempotency_key"],
            status=PublicationStatus(row["status"]),
            dry_run=bool(row["dry_run"]),
            claim_owner=row["claim_owner"],
            claimed_at=(datetime.fromisoformat(row["claimed_at"]) if row["claimed_at"] else None),
            lease_expires_at=(
                datetime.fromisoformat(row["lease_expires_at"]) if row["lease_expires_at"] else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_publication_attempt(row: sqlite3.Row) -> PublicationAttempt:
        return PublicationAttempt(
            id=row["id"],
            publication_id=row["publication_id"],
            attempt_number=row["attempt_number"],
            adapter_name=row["adapter_name"],
            started_at=datetime.fromisoformat(row["started_at"]),
            finished_at=datetime.fromisoformat(row["finished_at"]),
            outcome=PublicationAttemptOutcome(row["outcome"]),
            error_classification=row["error_classification"],
            remote_identifier=row["remote_identifier"],
        )

    @staticmethod
    def _row_to_publication_receipt(row: sqlite3.Row) -> PublicationReceipt:
        return PublicationReceipt(
            id=row["id"],
            publication_id=row["publication_id"],
            platform=row["platform"],
            item_index=row["item_index"],
            remote_id=row["remote_id"],
            remote_url=row["remote_url"],
            published_at=datetime.fromisoformat(row["published_at"]),
            adapter_name=row["adapter_name"],
            adapter_version=row["adapter_version"],
            status=row["status"],
            delivery_kind=row["delivery_kind"],
            metadata=MappingProxyType(json.loads(row["receipt_metadata"])),
        )

    @staticmethod
    def _row_to_metric_definition(row: sqlite3.Row) -> MetricDefinition:
        return MetricDefinition(
            key=row["metric_key"],
            platform=row["platform"],
            label=row["label"],
            description=row["description"],
            unit=MetricUnit(row["unit"]),
            family=MetricFamily(row["family"]),
            aggregation=AggregationBehavior(row["aggregation_behavior"]),
            source=row["source"],
            version=row["version"],
        )

    @staticmethod
    def _row_to_analytics_run(row: sqlite3.Row) -> AnalyticsCollectionRun:
        return AnalyticsCollectionRun(
            id=row["id"],
            idempotency_key=row["idempotency_key"],
            platform=row["platform"],
            publication_receipt_id=row["publication_receipt_id"],
            job_id=row["job_id"],
            started_at=datetime.fromisoformat(row["started_at"]),
            completed_at=(
                datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None
            ),
            outcome=AnalyticsRunOutcome(row["outcome"]),
            adapter_name=row["adapter_name"],
            adapter_version=row["adapter_version"],
            error_classification=row["error_classification"],
            metrics_collected_count=row["metrics_collected_count"],
            unavailable_metric_keys=tuple(json.loads(row["unavailable_metric_keys"])),
            retry_count=row["retry_count"],
        )

    @staticmethod
    def _row_to_metric_snapshot(row: sqlite3.Row) -> MetricSnapshot:
        return MetricSnapshot(
            id=row["id"],
            collection_run_id=row["collection_run_id"],
            publication_receipt_id=row["publication_receipt_id"],
            job_id=row["job_id"],
            platform_content_id=row["platform_content_id"],
            platform=row["platform"],
            metric_key=row["metric_key"],
            value=Decimal(row["metric_value"]),
            observed_at=datetime.fromisoformat(row["observed_at"]),
            period_start=(
                datetime.fromisoformat(row["period_start"]) if row["period_start"] else None
            ),
            period_end=(datetime.fromisoformat(row["period_end"]) if row["period_end"] else None),
            source=row["source"],
            source_version=row["source_version"],
            collected_at=datetime.fromisoformat(row["collected_at"]),
            metadata=MappingProxyType(json.loads(row["snapshot_metadata"])),
        )

    @staticmethod
    def _row_to_media_asset(row: sqlite3.Row) -> MediaAsset:
        return MediaAsset(
            id=row["id"],
            job_id=row["job_id"],
            platform_content_id=row["platform_content_id"],
            media_type=MediaAssetType(row["media_type"]),
            source_url=row["source_url"],
            order=row["media_order"],
            alt_text=row["alt_text"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _generation_metadata_from_json(raw_value: str | None) -> GenerationMetadata | None:
        if raw_value is None:
            return None
        payload = json.loads(raw_value)
        return GenerationMetadata(
            provider=payload["provider"],
            model=payload["model"],
            task=payload["task"],
            generated_at=datetime.fromisoformat(payload["generated_at"]),
            duration_ms=payload["duration_ms"],
            requested_at=(
                datetime.fromisoformat(payload["requested_at"])
                if payload.get("requested_at") is not None
                else None
            ),
            provider_latency_ms=payload.get("provider_latency_ms"),
            retry_count=payload.get("retry_count", 0),
            input_tokens=payload.get("input_tokens"),
            output_tokens=payload.get("output_tokens"),
            total_tokens=payload.get("total_tokens"),
            estimated_cost=payload.get("estimated_cost"),
            cost_class=payload.get("cost_class", "unknown"),
        )
