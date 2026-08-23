import tempfile
import unittest
from pathlib import Path

from lorchestrateur.domain.workflow import ContentJob, ContentJobState, StateMachine
from lorchestrateur.persistence.contracts import ConcurrentUpdateError
from lorchestrateur.persistence.sqlite import SQLiteContentJobRepository


class SQLiteRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = Path(self.temporary_directory.name) / "test.db"
        self.repository = SQLiteContentJobRepository(database_path)
        self.machine = StateMachine()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_job_and_transition_trace_round_trip(self) -> None:
        job = ContentJob.create(
            workspace_id="workspace-1",
            idea="An idea",
            target_platforms=("blog",),
            job_id="job-1",
        )
        self.repository.add(job)
        updated, step = self.machine.transition(job, ContentJobState.RESEARCHING)
        self.repository.save(updated, step)

        restored = self.repository.get(job.id)
        steps = self.repository.list_steps(job.id)

        self.assertEqual(restored, updated)
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].event, "state_transition")
        self.assertEqual(steps[0].to_state, ContentJobState.RESEARCHING)

    def test_optimistic_version_check_rejects_stale_update(self) -> None:
        job = ContentJob.create(
            workspace_id="workspace-1",
            idea="An idea",
            target_platforms=("x",),
            job_id="job-2",
        )
        self.repository.add(job)
        first, first_step = self.machine.transition(job, ContentJobState.RESEARCHING)
        stale, stale_step = self.machine.transition(job, ContentJobState.RESEARCHING)

        self.repository.save(first, first_step)
        with self.assertRaises(ConcurrentUpdateError):
            self.repository.save(stale, stale_step)


if __name__ == "__main__":
    unittest.main()

