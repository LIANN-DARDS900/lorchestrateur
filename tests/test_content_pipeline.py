import unittest

from lorchestrateur.ai.contracts import AIRequest, AITask
from lorchestrateur.ai.fake import FakeAIProvider
from lorchestrateur.ai.router import AIRouter
from lorchestrateur.application.service import OrchestrationService
from lorchestrateur.domain.content import EvidenceStatus, SourceType
from lorchestrateur.domain.workflow import ContentJobState, StateMachine
from lorchestrateur.persistence.contracts import ArtifactNotFoundError
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.platforms.builtins import create_default_registry


SOURCE_ID = "source-1"


def valid_structured_output(request: AIRequest):
    if request.task is AITask.CONTENT_STRATEGY:
        return {
            "objective": "Explain controlled content orchestration",
            "target_audience": "Technical decision-makers",
            "angle": "Governance makes AI useful",
            "tone": "Professional and precise",
            "key_messages": [
                {
                    "message": "Explicit workflow boundaries improve traceability",
                    "source_ids": [SOURCE_ID],
                }
            ],
            "intended_outcome": "Readers understand the governed approach",
        }
    if request.task is AITask.MASTER_CONTENT:
        return {
            "title": "Governed content orchestration",
            "summary": "A concise explanation of evidence-aware content orchestration.",
            "body": (
                "Canonical master content explains why explicit workflow boundaries, "
                "reviewed evidence, and deterministic validation improve traceability."
            ),
            "key_points": ["Explicit workflow boundaries improve traceability"],
            "source_ids": [SOURCE_ID],
        }
    return None


class ContentIntelligencePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryContentJobRepository()
        self.fake = FakeAIProvider(structured_handler=valid_structured_output)
        self.service = OrchestrationService(
            self.repository,
            StateMachine(),
            create_default_registry(),
            ai_router=AIRouter((self.fake,), provider_order=("fake",)),
        )

    def prepare_research(self, service=None, job_id: str = "job-1"):
        active_service = service or self.service
        job = active_service.create_job(
            workspace_id="workspace-1",
            idea="Explain controlled content orchestration",
            target_platforms=("blog", "x"),
            job_id=job_id,
        )
        active_service.begin_research(job.id)
        active_service.add_source(
            job.id,
            source_id=SOURCE_ID,
            title="Architecture source",
            url="https://example.com/source",
            source_type=SourceType.WEB,
            relevant_excerpt="Explicit workflow boundaries improve traceability.",
            evidence_status=EvidenceStatus.REVIEWED,
        )
        return job

    def test_full_pipeline_persists_strategy_and_master_content(self) -> None:
        job = self.prepare_research()

        research = self.service.complete_research(job.id)
        strategy = self.service.generate_content_strategy(job.id)
        master = self.service.generate_master_content(job.id)

        self.assertFalse(research.paused)
        self.assertFalse(strategy.paused)
        self.assertFalse(master.paused)
        self.assertEqual(master.job.state, ContentJobState.ADAPTING_PLATFORMS)
        self.assertEqual(
            self.repository.get_strategy(job.id), strategy.strategy
        )
        self.assertEqual(
            self.repository.get_master_content(job.id), master.master_content
        )
        self.assertEqual(
            [request.output_schema.value for request in self.fake.requests],
            ["content_strategy_v1", "master_content_v1"],
        )

        steps = self.repository.list_steps(job.id)
        self.assertEqual(
            [step.event for step in steps],
            [
                "research_started",
                "source_evidence_added",
                "research_completed",
                "content_strategy_persisted",
                "master_content_persisted",
            ],
        )
        trace_text = repr([dict(step.details) for step in steps])
        self.assertNotIn("Canonical master content explains", trace_text)
        self.assertNotIn("Create canonical master content", trace_text)

    def test_research_without_reviewed_evidence_pauses(self) -> None:
        job = self.service.create_job(
            workspace_id="workspace-1",
            idea="An idea",
            target_platforms=("blog",),
            job_id="unreviewed-job",
        )
        self.service.begin_research(job.id)
        self.service.add_source(
            job.id,
            source_id="unreviewed-source",
            title="Unreviewed input",
            source_type=SourceType.MANUAL,
            relevant_excerpt="A claim that has not been reviewed.",
        )

        outcome = self.service.complete_research(job.id)

        self.assertTrue(outcome.paused)
        self.assertEqual(outcome.job.paused_from, ContentJobState.RESEARCHING)
        self.assertEqual(outcome.validation.issues[0].code, "reviewed_source_required")

    def test_unavailable_provider_pauses_strategy_generation(self) -> None:
        unavailable = FakeAIProvider(provider_name="local", available=False)
        service = OrchestrationService(
            self.repository,
            StateMachine(),
            create_default_registry(),
            ai_router=AIRouter((unavailable,), provider_order=("local",)),
        )
        job = self.prepare_research(service, "unavailable-job")
        service.complete_research(job.id)

        outcome = service.generate_content_strategy(job.id)

        self.assertTrue(outcome.paused)
        self.assertEqual(outcome.job.paused_from, ContentJobState.STRATEGIZING)
        with self.assertRaises(ArtifactNotFoundError):
            self.repository.get_strategy(job.id)

    def test_invalid_structured_strategy_output_pauses(self) -> None:
        invalid = FakeAIProvider(structured_output={"objective": "Incomplete"})
        service = OrchestrationService(
            self.repository,
            StateMachine(),
            create_default_registry(),
            ai_router=AIRouter((invalid,), provider_order=("fake",)),
        )
        job = self.prepare_research(service, "invalid-output-job")
        service.complete_research(job.id)

        outcome = service.generate_content_strategy(job.id)

        self.assertTrue(outcome.paused)
        self.assertEqual(outcome.job.state, ContentJobState.PAUSED)
        self.assertEqual(outcome.validation.issues[0].code, "invalid_required_field")

    def test_unknown_strategy_source_reference_pauses(self) -> None:
        def unknown_reference(request: AIRequest):
            output = valid_structured_output(request)
            if request.task is AITask.CONTENT_STRATEGY:
                output["key_messages"][0]["source_ids"] = ["unknown-source"]
            return output

        invalid = FakeAIProvider(structured_handler=unknown_reference)
        service = OrchestrationService(
            self.repository,
            StateMachine(),
            create_default_registry(),
            ai_router=AIRouter((invalid,), provider_order=("fake",)),
        )
        job = self.prepare_research(service, "bad-reference-job")
        service.complete_research(job.id)

        outcome = service.generate_content_strategy(job.id)

        self.assertTrue(outcome.paused)
        self.assertEqual(
            outcome.validation.issues[0].code, "unknown_source_reference"
        )

    def test_invalid_structured_master_content_pauses(self) -> None:
        def invalid_master(request: AIRequest):
            if request.task is AITask.MASTER_CONTENT:
                return {"title": "Incomplete master content"}
            return valid_structured_output(request)

        invalid = FakeAIProvider(structured_handler=invalid_master)
        service = OrchestrationService(
            self.repository,
            StateMachine(),
            create_default_registry(),
            ai_router=AIRouter((invalid,), provider_order=("fake",)),
        )
        job = self.prepare_research(service, "invalid-master-job")
        service.complete_research(job.id)
        service.generate_content_strategy(job.id)

        outcome = service.generate_master_content(job.id)

        self.assertTrue(outcome.paused)
        self.assertEqual(outcome.job.paused_from, ContentJobState.GENERATING_MASTER)
        self.assertEqual(outcome.validation.issues[0].code, "invalid_required_field")


if __name__ == "__main__":
    unittest.main()
