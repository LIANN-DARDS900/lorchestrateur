"""SQLite adapter with atomic optimistic job updates and trace checkpoints."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import MappingProxyType

from lorchestrateur.domain.workflow import ContentJob, ContentJobState, JobStep
from lorchestrateur.persistence.contracts import (
    ConcurrentUpdateError,
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

                CREATE INDEX IF NOT EXISTS idx_job_steps_job_id
                    ON job_steps(job_id, sequence);

                PRAGMA user_version = 1;
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
        if step.job_id != job.id or step.sequence != job.version:
            raise ValueError("job step does not match the content job checkpoint")
        expected_version = job.version - 1
        with self._connect() as connection:
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

    def list_steps(self, job_id: str) -> tuple[JobStep, ...]:
        self.get(job_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM job_steps WHERE job_id = ? ORDER BY sequence", (job_id,)
            ).fetchall()
        return tuple(self._row_to_step(row) for row in rows)

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
