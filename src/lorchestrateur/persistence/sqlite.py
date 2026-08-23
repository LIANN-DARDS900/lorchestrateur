"""SQLite adapter with atomic optimistic job updates and trace checkpoints."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from lorchestrateur.domain.content import (
    ContentStrategy,
    EvidenceStatus,
    GenerationMetadata,
    MasterContent,
    SourceEvidence,
    SourceType,
    StrategyKeyMessage,
)
from lorchestrateur.domain.workflow import ContentJob, ContentJobState, JobStep
from lorchestrateur.persistence.contracts import (
    ArtifactNotFoundError,
    ConcurrentUpdateError,
    DuplicateArtifactError,
    DuplicateJobError,
    JobNotFoundError,
)


class UnsupportedDatabaseURLError(ValueError):
    pass


class SQLiteContentJobRepository:
    """Local repository; callers depend on the repository protocol, not SQLite APIs."""

    def __init__(self, database_path: str | Path, *, initialize: bool = True) -> None:
        self._database_path = Path(database_path)
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        if initialize:
            self.initialize()

    @classmethod
    def from_database_url(cls, database_url: str) -> SQLiteContentJobRepository:
        prefix = "sqlite:///"
        if not database_url.startswith(prefix):
            raise UnsupportedDatabaseURLError(
                "the SQLite adapter requires a database URL beginning with sqlite:///"
            )
        path = database_url.removeprefix(prefix)
        if not path:
            raise UnsupportedDatabaseURLError("SQLite database path cannot be empty")
        return cls(path)

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

                CREATE INDEX IF NOT EXISTS idx_job_steps_job_id
                    ON job_steps(job_id, sequence);

                CREATE INDEX IF NOT EXISTS idx_sources_job_id
                    ON sources(job_id);

                PRAGMA user_version = 2;
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
            row = connection.execute(
                "SELECT * FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
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
            raise ArtifactNotFoundError(
                f"content strategy not found for job: {job_id}"
            )
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
                        self._generation_metadata_to_json(
                            master_content.generation_metadata
                        ),
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
            raise ArtifactNotFoundError(
                f"master content not found for job: {job_id}"
            )
        return self._row_to_master_content(row)

    @staticmethod
    def _update_job(
        connection: sqlite3.Connection, job: ContentJob, step: JobStep
    ) -> None:
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
            },
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
            StrategyKeyMessage(
                message=item["message"], source_ids=tuple(item["source_ids"])
            )
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
            generation_metadata=cls._generation_metadata_from_json(
                row["generation_metadata"]
            ),
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
            generation_metadata=cls._generation_metadata_from_json(
                row["generation_metadata"]
            ),
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
        )
