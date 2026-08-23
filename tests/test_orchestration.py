import unittest

from lorchestrateur.ai.contracts import AIRequest, AITask
from lorchestrateur.ai.fake import FakeAIProvider
from lorchestrateur.ai.router import AIRouter
from lorchestrateur.application.service import OrchestrationService
from lorchestrateur.domain.workflow import ContentJobState, StateMachine
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.platforms.builtins import create_default_registry
from lorchestrateur.platforms.contracts import PlatformContent


class OrchestrationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryContentJobRepository()
        self.fake = FakeAIProvider()
        self.router = AIRouter((self.fake,), provider_order=("fake",))
        self.service = OrchestrationService(
            self.repository,
            StateMachine(),
            create_default_registry(),
            ai_router=self.router,
        )

    def create_job(self, job_id: str = "job-1"):
        return self.service.create_job(
            workspace_id="workspace-1",
            idea="A governed multi-channel idea",
            target_platforms=("x",),
            job_id=job_id,
        )

    def move_to_strategizing(self, job_id: str) -> None:
        self.service.transition(
            job_id, ContentJobState.RESEARCHING, event="research_started"
        )
        self.service.transition(
            job_id, ContentJobState.STRATEGIZING, event="research_completed"
        )

    def move_to_validating(self, job_id: str) -> None:
        self.move_to_strategizing(job_id)
        for target in (
            ContentJobState.GENERATING_MASTER,
            ContentJobState.ADAPTING_PLATFORMS,
            ContentJobState.VALIDATING,
        ):
            self.service.transition(job_id, target, event="test_stage_completed")

    def test_ai_stage_records_provider_trace(self) -> None:
        job = self.create_job()
        self.move_to_strategizing(job.id)

        outcome = self.service.complete_ai_stage(
            job.id,
            AIRequest(
                task=AITask.STRATEGIC_ANGLE,
                prompt="Develop a strategic angle",
                context={},
            ),
        )

        self.assertFalse(outcome.paused)
        self.assertEqual(outcome.job.state, ContentJobState.GENERATING_MASTER)
        last_step = self.repository.list_steps(job.id)[-1]
        self.assertEqual(last_step.event, "ai_stage_completed")
        self.assertEqual(last_step.details["provider"], "fake")
        self.assertNotIn("prompt", last_step.details)

    def test_unavailable_free_ai_pauses_instead_of_using_paid_provider(self) -> None:
        paid = FakeAIProvider(provider_name="paid", paid=True)
        service = OrchestrationService(
            self.repository,
            StateMachine(),
            create_default_registry(),
            ai_router=AIRouter((paid,), provider_order=("paid",)),
        )
        job = service.create_job(
            workspace_id="workspace-1",
            idea="An idea",
            target_platforms=("x",),
            job_id="paid-policy-job",
        )
        service.transition(job.id, ContentJobState.RESEARCHING, event="started")
        service.transition(job.id, ContentJobState.STRATEGIZING, event="researched")

        outcome = service.complete_ai_stage(
            job.id,
            AIRequest(task=AITask.STRATEGIC_ANGLE, prompt="Find an angle", context={}),
        )

        self.assertTrue(outcome.paused)
        self.assertEqual(outcome.job.paused_from, ContentJobState.STRATEGIZING)
        self.assertEqual(paid.requests, [])

    def test_validation_allows_only_one_controlled_repair(self) -> None:
        job = self.create_job("repair-job")
        self.move_to_validating(job.id)
        invalid_content = {
            "x": PlatformContent(platform="x", fields={"text": "x" * 281})
        }

        first = self.service.validate_platform_content(job.id, invalid_content)
        self.assertTrue(first.repair_requested)
        self.assertEqual(first.job.repair_attempts, 1)

        self.service.complete_ai_stage(
            job.id,
            AIRequest(
                task=AITask.CONTROLLED_REWRITE,
                prompt="Repair the content once",
                context={},
            ),
        )
        second = self.service.validate_platform_content(job.id, invalid_content)

        self.assertTrue(second.paused)
        self.assertFalse(second.repair_requested)
        self.assertEqual(second.job.state, ContentJobState.PAUSED)

    def test_valid_content_awaits_human_approval(self) -> None:
        job = self.create_job("valid-job")
        self.move_to_validating(job.id)

        outcome = self.service.validate_platform_content(
            job.id,
            {"x": PlatformContent(platform="x", fields={"text": "Clear and concise"})},
        )
        approved = self.service.approve(outcome.job.id, approved_by="editor@example.com")

        self.assertEqual(outcome.job.state, ContentJobState.AWAITING_APPROVAL)
        self.assertEqual(approved.state, ContentJobState.APPROVED)
        self.assertEqual(
            self.repository.list_steps(job.id)[-1].event,
            "human_approval_recorded",
        )

    def test_failure_is_persisted_with_reason(self) -> None:
        job = self.create_job("failed-job")

        failed = self.service.fail(job.id, reason="invalid source material")

        self.assertEqual(failed.state, ContentJobState.FAILED)
        self.assertEqual(failed.status_message, "invalid source material")
        self.assertEqual(self.repository.get(job.id), failed)


if __name__ == "__main__":
    unittest.main()

