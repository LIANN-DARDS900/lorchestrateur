import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from lorchestrateur.ai.contracts import (
    AIOutputSchema,
    AIProviderError,
    AIRequest,
    AITask,
)
from lorchestrateur.ai.fake import FakeAIProvider
from lorchestrateur.ai.router import AIRouter
from lorchestrateur.application.service import (
    OrchestrationService,
    PlatformContentBatchError,
)
from lorchestrateur.domain.content import (
    EvidenceStatus,
    GenerationMetadata,
    SourceType,
)
from lorchestrateur.domain.platform_content import (
    PlatformContentRecord,
    PlatformValidationStatus,
    QualityPolicy,
)
from lorchestrateur.domain.workflow import ContentJobState, StateMachine
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.persistence.sqlite import SQLiteContentJobRepository
from lorchestrateur.platforms.blog import BLOG, BlogContentV1
from lorchestrateur.platforms.builtins import create_default_registry
from lorchestrateur.platforms.contracts import PlatformContent
from lorchestrateur.platforms.facebook import FacebookContentV1
from lorchestrateur.platforms.instagram import (
    InstagramCarouselV1,
    InstagramReelV1,
)
from lorchestrateur.platforms.x import XContentV1, XFormat

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)
SOURCE_ID = "source-1"


def valid_output(request: AIRequest):
    if request.output_schema is AIOutputSchema.CONTENT_STRATEGY_V1:
        return {
            "objective": "Explain governed platform adaptation",
            "target_audience": "Content operations leaders",
            "angle": "Quality governance enables safe reuse",
            "tone": "Professional and precise",
            "key_messages": [
                {
                    "message": "Deterministic gates preserve traceability",
                    "source_ids": [SOURCE_ID],
                }
            ],
            "intended_outcome": "Readers understand controlled adaptation",
        }
    if request.output_schema is AIOutputSchema.MASTER_CONTENT_V1:
        return {
            "title": "Governed platform adaptation",
            "summary": "A canonical explanation of controlled multi-channel adaptation.",
            "body": (
                "A persisted canonical artifact lets each channel change structure and tone "
                "without changing the approved factual foundation."
            ),
            "key_points": ["Deterministic gates preserve traceability"],
            "source_ids": [SOURCE_ID],
        }
    if request.output_schema is AIOutputSchema.BLOG_CONTENT_V1:
        return {
            "platform": "blog",
            "schema_version": "blog_content_v1",
            "format": "article",
            "title": "How governed platform adaptation works",
            "slug_suggestion": "governed-platform-adaptation",
            "excerpt": "Turn one approved argument into channel-specific content safely.",
            "introduction": "Multi-channel work needs one factual foundation.",
            "sections": [
                {
                    "heading": "Start with canonical content",
                    "body": "Persist the reviewed argument before adapting its presentation.",
                },
                {
                    "heading": "Apply deterministic gates",
                    "body": "Validate references, structure, and platform limits in code.",
                },
            ],
            "conclusion": "Governance makes adaptation repeatable and reviewable.",
            "cta": "Review the channel plan before approval.",
            "seo_title": "Governed Multi-Channel Adaptation",
            "meta_description": (
                "Learn how canonical content, typed channel contracts, and deterministic "
                "quality gates support governed multi-channel adaptation."
            ),
            "source_references": [SOURCE_ID],
            "internal_link_suggestions": ["Content governance overview"],
        }
    if request.output_schema is AIOutputSchema.X_CONTENT_V1:
        return {
            "platform": "x",
            "schema_version": "x_content_v1",
            "format": "single_post",
            "opening_hook": "One source of truth, four channel-native outputs.",
            "posts": [
                {
                    "order": 1,
                    "text": (
                        "Multi-channel adaptation works when every variant starts from the "
                        "same reviewed master, then passes deterministic quality gates."
                    ),
                }
            ],
            "cta": "Which channel constraint matters most to your team?",
            "source_references": [SOURCE_ID],
        }
    if request.output_schema is AIOutputSchema.INSTAGRAM_CONTENT_V1:
        return {
            "platform": "instagram",
            "schema_version": "instagram_content_v1",
            "format": "carousel",
            "hook": "One approved idea. Four native formats.",
            "slides": [
                {"order": 1, "heading": "Start", "body": "Approve the master argument."},
                {"order": 2, "heading": "Adapt", "body": "Change format, not facts."},
                {"order": 3, "heading": "Gate", "body": "Validate before approval."},
            ],
            "caption": "Platform fit should never weaken evidence integrity.",
            "cta": "Save this governance checklist.",
            "source_references": [SOURCE_ID],
        }
    if request.output_schema is AIOutputSchema.FACEBOOK_CONTENT_V1:
        return {
            "platform": "facebook",
            "schema_version": "facebook_content_v1",
            "format": "story_post",
            "opening": "A content team starts with one reviewed argument.",
            "body": (
                "Instead of pasting the same copy everywhere, it reshapes the story for each "
                "community while deterministic checks keep the evidence boundary intact."
            ),
            "cta": "How does your team review channel variants?",
            "link_context_recommendation": "Link to the full governance article when available.",
            "source_references": [SOURCE_ID],
        }
    return None


class FailXOnceProvider(FakeAIProvider):
    fail_x_once: bool = True

    def generate(self, request: AIRequest):
        if request.output_schema is AIOutputSchema.X_CONTENT_V1 and self.fail_x_once:
            raise AIProviderError("temporary local provider failure")
        return super().generate(request)


class PlatformAdaptationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryContentJobRepository()
        self.provider = FakeAIProvider(structured_handler=valid_output)
        self.service = self._service(self.repository, self.provider)

    @staticmethod
    def _service(
        repository,
        provider: FakeAIProvider,
        *,
        quality_policy: QualityPolicy | None = None,
    ) -> OrchestrationService:
        return OrchestrationService(
            repository,
            StateMachine(clock=lambda: NOW),
            create_default_registry(),
            ai_router=AIRouter((provider,), provider_order=(provider.name,)),
            quality_policy=quality_policy,
            clock=lambda: NOW,
        )

    @staticmethod
    def _prepare(service: OrchestrationService, job_id: str, platforms: tuple[str, ...]):
        job = service.create_job(
            workspace_id="workspace-1",
            idea="Explain governed platform adaptation",
            target_platforms=platforms,
            job_id=job_id,
        )
        service.begin_research(job.id)
        service.add_source(
            job.id,
            source_id=SOURCE_ID,
            title="Governance source",
            url="https://example.com/governance",
            source_type=SourceType.WEB,
            relevant_excerpt="Deterministic gates preserve traceability.",
            evidence_status=EvidenceStatus.REVIEWED,
        )
        service.complete_research(job.id)
        service.generate_content_strategy(job.id)
        service.generate_master_content(job.id)
        return job

    def test_all_platforms_are_typed_persisted_and_approval_ready(self) -> None:
        job = self._prepare(self.service, "all-platforms", ("blog", "x", "instagram", "facebook"))

        adaptation = self.service.adapt_platforms(job.id)
        evaluation = self.service.evaluate_platform_adaptations(job.id)

        self.assertEqual(adaptation.job.state, ContentJobState.VALIDATING)
        self.assertEqual(evaluation.job.state, ContentJobState.AWAITING_APPROVAL)
        self.assertIsInstance(evaluation.contents["blog"].payload, BlogContentV1)
        self.assertIsInstance(evaluation.contents["x"].payload, XContentV1)
        self.assertIsInstance(evaluation.contents["instagram"].payload, InstagramCarouselV1)
        self.assertIsInstance(evaluation.contents["facebook"].payload, FacebookContentV1)
        self.assertEqual(
            {content.schema_version for content in evaluation.contents.values()},
            {
                "blog_content_v1",
                "x_content_v1",
                "instagram_content_v1",
                "facebook_content_v1",
            },
        )
        master = self.repository.get_master_content(job.id)
        self.assertTrue(
            all(content.master_content_id == master.id for content in evaluation.contents.values())
        )
        self.assertTrue(
            all(content.quality_score == 100 for content in evaluation.contents.values())
        )

        trace = repr([dict(step.details) for step in self.repository.list_steps(job.id)])
        self.assertNotIn("Multi-channel adaptation works when", trace)
        self.assertNotIn("Return exactly", trace)

    def test_x_thread_adaptation(self) -> None:
        def thread_output(request: AIRequest):
            output = valid_output(request)
            if request.output_schema is AIOutputSchema.X_CONTENT_V1:
                output.update(
                    {
                        "format": "thread",
                        "posts": [
                            {"order": 1, "text": "Start from one reviewed master."},
                            {"order": 2, "text": "Adapt structure for the channel."},
                            {"order": 3, "text": "Validate before human approval."},
                        ],
                    }
                )
            return output

        provider = FakeAIProvider(structured_handler=thread_output)
        service = self._service(self.repository, provider)
        job = self._prepare(service, "x-thread", ("x",))

        service.adapt_platforms(job.id)
        result = service.evaluate_platform_adaptations(job.id)

        payload = result.contents["x"].payload
        self.assertIsInstance(payload, XContentV1)
        self.assertEqual(payload.format, XFormat.THREAD)
        self.assertEqual([post.order for post in payload.posts], [1, 2, 3])

    def test_instagram_reel_plan_adaptation(self) -> None:
        def reel_output(request: AIRequest):
            output = valid_output(request)
            if request.output_schema is AIOutputSchema.INSTAGRAM_CONTENT_V1:
                return {
                    "platform": "instagram",
                    "schema_version": "instagram_content_v1",
                    "format": "reel_concept",
                    "opening_hook": "What if reuse did not mean copy and paste?",
                    "beats": [
                        {"order": 1, "scene": "Master artifact", "message": "One truth"},
                        {"order": 2, "scene": "Channel cards", "message": "Native plans"},
                    ],
                    "caption": "A creative plan can be native and evidence-aware.",
                    "cta": "Share this with your content operations team.",
                    "source_references": [SOURCE_ID],
                }
            return output

        provider = FakeAIProvider(structured_handler=reel_output)
        service = self._service(self.repository, provider)
        job = self._prepare(service, "instagram-reel", ("instagram",))

        service.adapt_platforms(job.id)
        result = service.evaluate_platform_adaptations(job.id)

        self.assertIsInstance(result.contents["instagram"].payload, InstagramReelV1)
        self.assertEqual(result.contents["instagram"].format, "reel_concept")

    def test_reference_integrity_rejects_unknown_source(self) -> None:
        def bad_reference(request: AIRequest):
            output = valid_output(request)
            if request.output_schema is AIOutputSchema.X_CONTENT_V1:
                output["source_references"] = ["invented-source"]
            return output

        provider = FakeAIProvider(structured_handler=bad_reference)
        service = self._service(self.repository, provider)
        job = self._prepare(service, "bad-reference", ("x",))

        service.adapt_platforms(job.id)
        result = service.evaluate_platform_adaptations(job.id)

        self.assertTrue(result.repair_requested)
        self.assertEqual(result.job.state, ContentJobState.ADAPTING_PLATFORMS)
        self.assertEqual(
            result.contents["x"].validation_status,
            PlatformValidationStatus.FAILED,
        )
        self.assertIn(
            "unknown_source_reference",
            [issue.code for issue in result.reports["x"].issues],
        )

    def test_malformed_output_uses_repair_budget_without_persisting_payload(self) -> None:
        def malformed(request: AIRequest):
            output = valid_output(request)
            if request.output_schema is AIOutputSchema.X_CONTENT_V1:
                return {
                    "platform": "x",
                    "schema_version": "x_content_v1",
                    "format": "single_post",
                }
            return output

        provider = FakeAIProvider(structured_handler=malformed)
        service = self._service(self.repository, provider)
        job = self._prepare(service, "malformed", ("x",))

        result = service.adapt_platforms(job.id)

        self.assertTrue(result.repair_requested)
        self.assertEqual(result.job.repair_attempts, 1)
        self.assertEqual(self.repository.list_platform_contents(job.id), ())
        self.assertEqual(result.issues["x"].issues[0].code, "invalid_required_field")

    def test_missing_requested_platform_cannot_await_approval(self) -> None:
        job = self._prepare(self.service, "missing-platform", ("blog", "x"))
        current = self.repository.get(job.id)
        master = self.repository.get_master_content(job.id)
        payload = BLOG.parse_payload(
            valid_output(
                AIRequest(
                    task=AITask.PLATFORM_ADAPTATION,
                    prompt="Create blog",
                    context={},
                    output_schema=AIOutputSchema.BLOG_CONTENT_V1,
                )
            )
        )
        content = PlatformContentRecord(
            id="only-blog",
            job_id=job.id,
            master_content_id=master.id,
            platform="blog",
            format=payload.format,
            schema_version=payload.schema_version,
            payload=payload,
            generation_metadata=GenerationMetadata(
                provider="fake",
                model="fake-v1",
                task="platform_adaptation",
                generated_at=NOW,
                duration_ms=1,
            ),
            generation_attempt_id="manual-partial-attempt",
            validation_status=PlatformValidationStatus.PENDING,
            quality_score=None,
            quality_breakdown=None,
            validation_issues=(),
            revision=1,
            created_at=NOW,
            updated_at=NOW,
        )
        current, step = StateMachine(clock=lambda: NOW).record_event(
            current, event="platform_content_persisted"
        )
        self.repository.save_platform_content_with_checkpoint(content, current, step)
        self.service.transition(
            job.id, ContentJobState.VALIDATING, event="partial_adaptation_finished"
        )

        result = self.service.evaluate_platform_adaptations(job.id)

        self.assertTrue(result.repair_requested)
        self.assertIn(
            "missing_platform_content",
            [issue.code for issue in result.reports["x"].issues],
        )

    def test_legacy_transient_validation_cannot_bypass_durable_records(self) -> None:
        job = self._prepare(self.service, "legacy-bypass", ("x",))
        self.service.transition(
            job.id, ContentJobState.VALIDATING, event="test_skip_adaptation"
        )

        with self.assertRaises(PlatformContentBatchError):
            self.service.validate_platform_content(
                job.id,
                {"x": PlatformContent(platform="x", fields={"text": "transient"})},
            )

    def test_quality_threshold_is_configurable_and_explainable(self) -> None:
        def no_cta(request: AIRequest):
            output = valid_output(request)
            if request.output_schema is AIOutputSchema.FACEBOOK_CONTENT_V1:
                output["cta"] = None
            return output

        provider = FakeAIProvider(structured_handler=no_cta)
        service = self._service(
            self.repository, provider, quality_policy=QualityPolicy(minimum_score=100)
        )
        job = self._prepare(service, "quality-threshold", ("facebook",))
        service.adapt_platforms(job.id)

        result = service.evaluate_platform_adaptations(job.id)

        content = result.contents["facebook"]
        self.assertEqual(content.quality_score, 95)
        self.assertEqual(content.quality_breakdown.completeness, 15)
        self.assertEqual(content.validation_status, PlatformValidationStatus.PASSED)
        self.assertTrue(result.repair_requested)
        self.assertEqual(result.reports["facebook"].issues[0].code, "quality_below_threshold")

    def test_one_targeted_repair_creates_revision_two(self) -> None:
        def repairable(request: AIRequest):
            output = valid_output(request)
            if request.output_schema is AIOutputSchema.X_CONTENT_V1:
                if request.task is AITask.PLATFORM_ADAPTATION:
                    output["posts"][0]["text"] = "x" * 281
                else:
                    self.assertIn(
                        "standard_post_too_long", request.context["repair"]["issue_codes"]
                    )
            return output

        provider = FakeAIProvider(structured_handler=repairable)
        service = self._service(self.repository, provider)
        job = self._prepare(service, "repair-success", ("x",))
        service.adapt_platforms(job.id)
        first = service.evaluate_platform_adaptations(job.id)
        self.assertTrue(first.repair_requested)

        second_adaptation = service.adapt_platforms(job.id)
        second = service.evaluate_platform_adaptations(job.id)

        self.assertEqual(second_adaptation.generated_platforms, ("x",))
        self.assertEqual(second.job.state, ContentJobState.AWAITING_APPROVAL)
        revisions = self.repository.list_platform_contents(job.id, platform="x")
        self.assertEqual([content.revision for content in revisions], [1, 2])

    def test_repair_exhaustion_pauses(self) -> None:
        def always_invalid(request: AIRequest):
            output = valid_output(request)
            if request.output_schema is AIOutputSchema.X_CONTENT_V1:
                output["posts"][0]["text"] = "x" * 281
            return output

        provider = FakeAIProvider(structured_handler=always_invalid)
        service = self._service(self.repository, provider)
        job = self._prepare(service, "repair-exhausted", ("x",))
        service.adapt_platforms(job.id)
        service.evaluate_platform_adaptations(job.id)
        service.adapt_platforms(job.id)

        result = service.evaluate_platform_adaptations(job.id)

        self.assertTrue(result.paused)
        self.assertFalse(result.repair_requested)
        self.assertEqual(result.job.state, ContentJobState.PAUSED)
        self.assertEqual(result.job.paused_from, ContentJobState.VALIDATING)

    def test_ai_unavailable_and_paid_disabled_pause_safely(self) -> None:
        job = self._prepare(self.service, "paid-disabled", ("x",))
        request_count = len(self.provider.requests)
        self.provider.paid = True

        result = self.service.adapt_platforms(job.id)

        self.assertTrue(result.paused)
        self.assertEqual(result.job.paused_from, ContentJobState.ADAPTING_PLATFORMS)
        self.assertEqual(len(self.provider.requests), request_count)
        self.assertEqual(self.repository.list_platform_contents(job.id), ())

    def test_partial_retry_reuses_same_attempt_without_duplicate(self) -> None:
        provider = FailXOnceProvider(structured_handler=valid_output)
        service = self._service(self.repository, provider)
        job = self._prepare(service, "retry-idempotency", ("blog", "x"))

        first = service.adapt_platforms(job.id)
        self.assertTrue(first.paused)
        self.assertEqual(len(self.repository.list_platform_contents(job.id)), 1)
        provider.fail_x_once = False
        service.resume(job.id)

        second = service.adapt_platforms(job.id)
        evaluation = service.evaluate_platform_adaptations(job.id)

        self.assertEqual(second.reused_platforms, ("blog",))
        self.assertEqual(second.generated_platforms, ("x",))
        self.assertEqual(len(self.repository.list_platform_contents(job.id)), 2)
        self.assertEqual(evaluation.job.state, ContentJobState.AWAITING_APPROVAL)

    def test_sqlite_round_trip_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database_path = Path(directory) / "phase3.db"
            repository = SQLiteContentJobRepository(database_path)
            provider = FakeAIProvider(structured_handler=valid_output)
            service = self._service(repository, provider)
            job = self._prepare(service, "sqlite-phase3", ("blog", "x"))
            service.adapt_platforms(job.id)
            evaluation = service.evaluate_platform_adaptations(job.id)

            reopened = SQLiteContentJobRepository(database_path)
            persisted = reopened.list_platform_contents(job.id)

            self.assertEqual(reopened.get(job.id).state, ContentJobState.AWAITING_APPROVAL)
            self.assertEqual(len(persisted), 2)
            self.assertEqual({content.platform for content in persisted}, {"blog", "x"})
            self.assertTrue(
                all(
                    content.master_content_id
                    == evaluation.contents[content.platform].master_content_id
                    for content in persisted
                )
            )


if __name__ == "__main__":
    unittest.main()
