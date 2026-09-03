import json
import unittest

from lorchestrateur.ai.contracts import AIOutputSchema, ProviderCostClass
from lorchestrateur.ai.providers.gemini import GeminiProvider, GeminiProviderConfig
from lorchestrateur.ai.providers.http import HTTPResponse
from lorchestrateur.ai.router import AIRouter
from lorchestrateur.application.service import OrchestrationService
from lorchestrateur.domain.content import EvidenceStatus, SourceType
from lorchestrateur.domain.workflow import ContentJobState, StateMachine
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.platforms.builtins import create_default_registry

SOURCE_ID = "source-1"


def structured_outputs() -> dict[AIOutputSchema, dict]:
    return {
        AIOutputSchema.CONTENT_STRATEGY_V1: {
            "objective": "Explain governed production AI",
            "target_audience": "Technical decision-makers",
            "angle": "Provider governance makes automation safer",
            "tone": "Professional and precise",
            "key_messages": [
                {
                    "message": "Deterministic controls remain authoritative",
                    "source_ids": [SOURCE_ID],
                }
            ],
            "intended_outcome": "Readers understand controlled AI execution",
        },
        AIOutputSchema.MASTER_CONTENT_V1: {
            "title": "Governed production AI",
            "summary": "Production AI remains bounded by deterministic controls.",
            "body": (
                "Provider adapters transform reviewed evidence while workflow, validation, "
                "quality, and approval policy remain deterministic."
            ),
            "key_points": ["Deterministic controls remain authoritative"],
            "source_ids": [SOURCE_ID],
        },
        AIOutputSchema.BLOG_CONTENT_V1: {
            "platform": "blog",
            "schema_version": "blog_content_v1",
            "format": "article",
            "title": "How governed production AI works",
            "slug_suggestion": "governed-production-ai",
            "excerpt": "Use real providers without surrendering deterministic control.",
            "introduction": "Production AI needs explicit execution boundaries.",
            "sections": [
                {
                    "heading": "Route by policy",
                    "body": "Free-first routing blocks undeclared cost before a request.",
                },
                {
                    "heading": "Validate deterministically",
                    "body": "Typed parsing and measurable gates remain authoritative.",
                },
            ],
            "conclusion": "Governance makes external generation reviewable.",
            "cta": "Review provider policy before enabling a model.",
            "seo_title": "Governed Production AI Providers",
            "meta_description": (
                "Learn how bounded provider adapters, structured output, and deterministic "
                "quality gates support governed production AI."
            ),
            "source_references": [SOURCE_ID],
            "internal_link_suggestions": ["Content governance overview"],
        },
        AIOutputSchema.X_CONTENT_V1: {
            "platform": "x",
            "schema_version": "x_content_v1",
            "format": "single_post",
            "opening_hook": "Real AI still needs deterministic boundaries.",
            "posts": [
                {
                    "order": 1,
                    "text": (
                        "Production AI is safer when provider policy, evidence integrity, "
                        "validation, and approval remain deterministic."
                    ),
                }
            ],
            "cta": "Which provider control matters most to your team?",
            "source_references": [SOURCE_ID],
        },
        AIOutputSchema.INSTAGRAM_CONTENT_V1: {
            "platform": "instagram",
            "schema_version": "instagram_content_v1",
            "format": "carousel",
            "hook": "Use real AI. Keep deterministic control.",
            "slides": [
                {"order": 1, "heading": "Configure", "body": "Declare provider cost."},
                {"order": 2, "heading": "Generate", "body": "Request typed output."},
                {"order": 3, "heading": "Gate", "body": "Validate before approval."},
            ],
            "caption": "Provider execution should never override content governance.",
            "cta": "Save this provider-governance checklist.",
            "source_references": [SOURCE_ID],
        },
        AIOutputSchema.FACEBOOK_CONTENT_V1: {
            "platform": "facebook",
            "schema_version": "facebook_content_v1",
            "format": "story_post",
            "opening": "A content team connects its first production AI provider.",
            "body": (
                "The provider writes, but deterministic policy still chooses eligibility, "
                "checks evidence, scores quality, and stops at human approval."
            ),
            "cta": "What would your team require before enabling production AI?",
            "link_context_recommendation": "Link to the provider-governance guide.",
            "source_references": [SOURCE_ID],
        },
    }


def gemini_http_response(content: dict) -> HTTPResponse:
    payload = {
        "candidates": [
            {
                "content": {"parts": [{"text": json.dumps(content)}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 20,
            "totalTokenCount": 30,
        },
    }
    return HTTPResponse(200, json.dumps(payload).encode("utf-8"), {})


class SchemaAwareTransport:
    def __init__(self) -> None:
        self.outputs = structured_outputs()
        self.calls = []

    def post_json(self, url, *, headers, payload, timeout_seconds):
        self.calls.append((url, dict(headers), payload, timeout_seconds))
        schema_name = payload["generationConfig"]["responseJsonSchema"]
        for output_schema, output in self.outputs.items():
            discriminator = output_schema.value
            if (
                discriminator
                == schema_name.get("properties", {})
                .get("schema_version", {})
                .get("enum", [None])[0]
            ):
                return gemini_http_response(output)
        required = set(schema_name.get("required", ()))
        if "objective" in required:
            return gemini_http_response(self.outputs[AIOutputSchema.CONTENT_STRATEGY_V1])
        if "key_points" in required:
            return gemini_http_response(self.outputs[AIOutputSchema.MASTER_CONTENT_V1])
        raise AssertionError("unexpected provider output schema")


class ProductionPipelineTests(unittest.TestCase):
    def test_mocked_gemini_executes_full_pipeline_to_awaiting_approval(self) -> None:
        repository = InMemoryContentJobRepository()
        transport = SchemaAwareTransport()
        provider = GeminiProvider(
            GeminiProviderConfig(
                api_key="test-only-secret",
                model="gemini-test-model",
                max_retries=0,
                cost_class=ProviderCostClass.FREE,
            ),
            transport=transport,
            sleeper=lambda _: None,
        )
        service = OrchestrationService(
            repository,
            StateMachine(),
            create_default_registry(),
            ai_router=AIRouter((provider,), provider_order=("gemini",)),
        )
        job = service.create_job(
            workspace_id="workspace-1",
            idea="How automation reduces repetitive IT operations",
            target_platforms=("blog", "x", "instagram", "facebook"),
            job_id="production-pipeline",
        )
        service.begin_research(job.id)
        service.add_source(
            job.id,
            source_id=SOURCE_ID,
            title="Reviewed operations evidence",
            url=None,
            source_type=SourceType.MANUAL,
            relevant_excerpt=(
                "Deterministic controls keep automated operations reviewable and traceable."
            ),
            evidence_status=EvidenceStatus.REVIEWED,
        )

        research = service.complete_research(job.id)
        strategy = service.generate_content_strategy(job.id)
        master = service.generate_master_content(job.id)
        adaptations = service.adapt_platforms(job.id)
        evaluation = service.evaluate_platform_adaptations(job.id)

        self.assertFalse(research.paused)
        self.assertFalse(strategy.paused)
        self.assertFalse(master.paused)
        self.assertFalse(adaptations.paused)
        self.assertEqual(evaluation.job.state, ContentJobState.AWAITING_APPROVAL)
        self.assertEqual(len(transport.calls), 6)
        self.assertTrue(all(call[0].startswith("https://") for call in transport.calls))
        self.assertEqual(master.master_content.generation_metadata.total_tokens, 30)
        self.assertTrue(
            all(
                content.generation_metadata.total_tokens == 30
                for content in evaluation.contents.values()
            )
        )
        trace = repr([dict(step.details) for step in repository.list_steps(job.id)])
        self.assertNotIn("test-only-secret", trace)
        self.assertNotIn("Provider adapters transform", trace)

    def test_unconfigured_production_provider_pauses_without_http(self) -> None:
        repository = InMemoryContentJobRepository()
        provider = GeminiProvider(
            GeminiProviderConfig(model="gemini-test-model", cost_class=ProviderCostClass.FREE)
        )
        service = OrchestrationService(
            repository,
            StateMachine(),
            create_default_registry(),
            ai_router=AIRouter((provider,), provider_order=("gemini",)),
        )
        job = service.create_job(
            workspace_id="workspace-1",
            idea="Governed provider availability",
            target_platforms=("blog",),
            job_id="unconfigured-production-provider",
        )
        service.begin_research(job.id)
        service.add_source(
            job.id,
            source_id=SOURCE_ID,
            title="Reviewed evidence",
            url=None,
            source_type=SourceType.MANUAL,
            relevant_excerpt="Governed systems fail closed when configuration is absent.",
            evidence_status=EvidenceStatus.REVIEWED,
        )
        service.complete_research(job.id)

        result = service.generate_content_strategy(job.id)

        self.assertTrue(result.paused)
        self.assertEqual(result.job.state, ContentJobState.PAUSED)
        last_step = repository.list_steps(job.id)[-1]
        self.assertEqual(last_step.details["attempts"][0]["outcome"], "not_configured")


if __name__ == "__main__":
    unittest.main()
