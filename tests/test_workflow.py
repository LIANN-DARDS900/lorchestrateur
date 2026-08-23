import unittest
from datetime import UTC, datetime

from lorchestrateur.domain.workflow import (
    ContentJob,
    ContentJobState,
    StateMachine,
    StateTransitionError,
)


NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


class WorkflowStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = StateMachine(clock=lambda: NOW, id_factory=lambda: "step-id")
        self.job = ContentJob.create(
            workspace_id="workspace-1",
            idea="A traceable content idea",
            target_platforms=("X", "blog", "x"),
            job_id="job-1",
            now=NOW,
        )

    def test_job_creation_normalizes_platforms(self) -> None:
        self.assertEqual(self.job.target_platforms, ("x", "blog"))
        self.assertEqual(self.job.state, ContentJobState.CREATED)
        self.assertEqual(self.job.version, 0)

    def test_forward_transition_emits_checkpoint(self) -> None:
        updated, step = self.machine.transition(
            self.job,
            ContentJobState.RESEARCHING,
            event="research_started",
            details={"source_limit": 5},
        )

        self.assertEqual(updated.state, ContentJobState.RESEARCHING)
        self.assertEqual(updated.version, 1)
        self.assertEqual(step.sequence, 1)
        self.assertEqual(step.from_state, ContentJobState.CREATED)
        self.assertEqual(step.details["source_limit"], 5)

    def test_invalid_transition_is_rejected(self) -> None:
        with self.assertRaises(StateTransitionError):
            self.machine.transition(self.job, ContentJobState.PUBLISHED)

    def test_pause_and_resume_restore_checkpoint_state(self) -> None:
        researching, _ = self.machine.transition(self.job, ContentJobState.RESEARCHING)
        paused, _ = self.machine.pause(researching, reason="dependency unavailable")
        resumed, step = self.machine.resume(paused)

        self.assertEqual(paused.paused_from, ContentJobState.RESEARCHING)
        self.assertEqual(resumed.state, ContentJobState.RESEARCHING)
        self.assertIsNone(resumed.paused_from)
        self.assertEqual(step.event, "workflow_resumed")

    def test_only_one_controlled_repair_is_allowed(self) -> None:
        job = self.job
        for target in (
            ContentJobState.RESEARCHING,
            ContentJobState.STRATEGIZING,
            ContentJobState.GENERATING_MASTER,
            ContentJobState.ADAPTING_PLATFORMS,
            ContentJobState.VALIDATING,
        ):
            job, _ = self.machine.transition(job, target)

        repair, step = self.machine.request_controlled_repair(job)
        self.assertEqual(repair.state, ContentJobState.ADAPTING_PLATFORMS)
        self.assertEqual(repair.repair_attempts, 1)
        self.assertEqual(step.event, "controlled_repair_requested")

        validating_again, _ = self.machine.transition(repair, ContentJobState.VALIDATING)
        stopped, step = self.machine.request_controlled_repair(validating_again)
        self.assertEqual(stopped.state, ContentJobState.PAUSED)
        self.assertEqual(step.event, "workflow_paused")
        self.assertIn("budget exhausted", stopped.status_message or "")

    def test_terminal_job_cannot_be_failed_again(self) -> None:
        failed, _ = self.machine.fail(self.job, reason="irrecoverable input")
        with self.assertRaises(StateTransitionError):
            self.machine.fail(failed, reason="again")


if __name__ == "__main__":
    unittest.main()

