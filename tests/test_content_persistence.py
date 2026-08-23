import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from lorchestrateur.domain.content import (
    ContentStrategy,
    EvidenceStatus,
    GenerationMetadata,
    MasterContent,
    SourceEvidence,
    SourceType,
    StrategyKeyMessage,
)
from lorchestrateur.domain.workflow import ContentJob, ContentJobState, StateMachine
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.persistence.sqlite import SQLiteContentJobRepository


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


class ContentArtifactPersistenceTests(unittest.TestCase):
    def test_memory_and_sqlite_round_trip_all_content_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repositories = (
                InMemoryContentJobRepository(),
                SQLiteContentJobRepository(Path(temporary_directory) / "content.db"),
            )
            for repository in repositories:
                with self.subTest(repository=type(repository).__name__):
                    self._assert_artifact_round_trip(repository)

    def _assert_artifact_round_trip(self, repository) -> None:
        machine = StateMachine(clock=lambda: NOW)
        job = ContentJob.create(
            workspace_id="workspace-1",
            idea="An evidence-aware content idea",
            target_platforms=("blog",),
            job_id=f"job-{type(repository).__name__}",
            now=NOW,
        )
        repository.add(job)
        job, step = machine.transition(job, ContentJobState.RESEARCHING)
        repository.save(job, step)

        evidence = SourceEvidence(
            id=f"source-{type(repository).__name__}",
            job_id=job.id,
            title="Architecture evidence",
            url="https://example.com/architecture",
            source_type=SourceType.WEB,
            relevant_excerpt="Explicit workflows provide traceability.",
            retrieved_at=NOW,
            evidence_status=EvidenceStatus.REVIEWED,
            metadata={"author": "Example Author"},
        )
        job, step = machine.record_event(job, event="source_evidence_added")
        repository.add_source_with_checkpoint(evidence, job, step)
        job, step = machine.transition(job, ContentJobState.STRATEGIZING)
        repository.save(job, step)

        generation = GenerationMetadata(
            provider="fake",
            model="fake-v1",
            task="content_strategy",
            generated_at=NOW,
            duration_ms=12,
        )
        content_strategy = ContentStrategy(
            id=f"strategy-{type(repository).__name__}",
            job_id=job.id,
            objective="Explain controlled orchestration",
            target_audience="Engineering leaders",
            angle="Deterministic governance",
            tone="Professional",
            key_messages=(
                StrategyKeyMessage(
                    message="Explicit workflows are traceable",
                    source_ids=(evidence.id,),
                ),
            ),
            intended_outcome="Readers understand the design",
            created_at=NOW,
            updated_at=NOW,
            generation_metadata=generation,
        )
        job, step = machine.transition(job, ContentJobState.GENERATING_MASTER)
        repository.save_strategy_with_checkpoint(content_strategy, job, step)

        master = MasterContent(
            id=f"master-{type(repository).__name__}",
            job_id=job.id,
            title="Controlled content orchestration",
            summary="A concise summary of the canonical argument.",
            body="The canonical body explains the evidence-aware architecture.",
            key_points=("Explicit workflows are traceable",),
            source_ids=(evidence.id,),
            created_at=NOW,
            updated_at=NOW,
            generation_metadata=GenerationMetadata(
                provider="fake",
                model="fake-v1",
                task="master_content",
                generated_at=NOW,
                duration_ms=18,
            ),
        )
        job, step = machine.transition(job, ContentJobState.ADAPTING_PLATFORMS)
        repository.save_master_content_with_checkpoint(master, job, step)

        self.assertEqual(repository.get_source(evidence.id), evidence)
        self.assertEqual(repository.list_sources(job.id), (evidence,))
        self.assertEqual(repository.get_strategy(job.id), content_strategy)
        self.assertEqual(repository.get_master_content(job.id), master)
        self.assertEqual(repository.get(job.id).state, ContentJobState.ADAPTING_PLATFORMS)
        self.assertEqual(len(repository.list_steps(job.id)), 5)


if __name__ == "__main__":
    unittest.main()
