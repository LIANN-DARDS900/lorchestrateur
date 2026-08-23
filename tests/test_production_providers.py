import json
import unittest
from datetime import UTC, datetime

from lorchestrateur.ai.contracts import (
    AIOutputSchema,
    AIRequest,
    AITask,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderCostClass,
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from lorchestrateur.ai.fake import FakeAIProvider
from lorchestrateur.ai.providers.gemini import GeminiProvider, GeminiProviderConfig
from lorchestrateur.ai.providers.http import HTTPResponse
from lorchestrateur.ai.providers.openrouter import (
    OpenRouterProvider,
    OpenRouterProviderConfig,
)
from lorchestrateur.ai.router import AIRouter, AIUnavailableError

NOW = datetime(2026, 8, 23, 12, 0, tzinfo=UTC)


def request() -> AIRequest:
    return AIRequest(
        task=AITask.MASTER_CONTENT,
        prompt="Create canonical content from the supplied evidence.",
        context={"idea": "Governed automation", "source_ids": ["source-1"]},
        max_output_characters=2_000,
        output_schema=AIOutputSchema.MASTER_CONTENT_V1,
    )


def x_request() -> AIRequest:
    return AIRequest(
        task=AITask.PLATFORM_ADAPTATION,
        prompt="Create one X post from the supplied evidence.",
        context={"source_ids": ["source-1"]},
        max_output_characters=2_000,
        output_schema=AIOutputSchema.X_CONTENT_V1,
    )


def master_payload() -> dict:
    return {
        "title": "Governed automation",
        "summary": "A concise evidence-aware summary.",
        "body": "Automation reduces repetitive work when deterministic controls remain explicit.",
        "key_points": ["Controls keep automation reviewable"],
        "source_ids": ["source-1"],
    }


def response(status: int, payload, *, headers=None) -> HTTPResponse:
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
    return HTTPResponse(status, body, headers or {})


def gemini_response(content=None, *, usage=None) -> dict:
    text = json.dumps(content if content is not None else master_payload())
    return {
        "candidates": [
            {
                "content": {"parts": [{"text": text}]},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": usage
        or {
            "promptTokenCount": 12,
            "candidatesTokenCount": 18,
            "totalTokenCount": 30,
        },
    }


def openrouter_response(content=None, *, usage=None) -> dict:
    text = json.dumps(content if content is not None else master_payload())
    return {
        "model": "openrouter/free-test",
        "choices": [
            {
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


class QueueTransport:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    def post_json(self, url, *, headers, payload, timeout_seconds):
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "payload": payload,
                "timeout_seconds": timeout_seconds,
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def gemini_provider(transport, *, retries=0, cost_class=ProviderCostClass.FREE):
    return GeminiProvider(
        GeminiProviderConfig(
            api_key="gemini-test-secret",
            model="gemini-test-model",
            timeout_seconds=7,
            max_retries=retries,
            cost_class=cost_class,
        ),
        transport=transport,
        sleeper=lambda _: None,
        timer=lambda: 1.0,
        clock=lambda: NOW,
    )


def openrouter_provider(transport, *, retries=0, cost_class=ProviderCostClass.FREE):
    return OpenRouterProvider(
        OpenRouterProviderConfig(
            api_key="openrouter-test-secret",
            model="openrouter/free-test",
            timeout_seconds=9,
            max_retries=retries,
            cost_class=cost_class,
        ),
        transport=transport,
        sleeper=lambda _: None,
        timer=lambda: 1.0,
        clock=lambda: NOW,
    )


class GeminiProviderTests(unittest.TestCase):
    def test_successful_structured_response_and_request_contract(self) -> None:
        transport = QueueTransport([response(200, gemini_response())])
        provider = gemini_provider(transport)

        result = provider.generate(request())

        self.assertEqual(result.structured_output, master_payload())
        self.assertEqual(result.provider, "gemini")
        self.assertEqual(transport.calls[0]["timeout_seconds"], 7)
        generation_config = transport.calls[0]["payload"]["generationConfig"]
        self.assertEqual(generation_config["responseMimeType"], "application/json")
        self.assertFalse(generation_config["responseJsonSchema"]["additionalProperties"])
        serialized_request = transport.calls[0]["payload"]["contents"][0]["parts"][0]["text"]
        self.assertIn('"task":"master_content"', serialized_request)

    def test_missing_credentials_is_not_configured(self) -> None:
        provider = GeminiProvider(GeminiProviderConfig(model="gemini-test-model"))

        self.assertFalse(provider.is_configured)
        with self.assertRaises(ProviderConfigurationError):
            provider.generate(request())

    def test_authentication_failure_is_not_retried(self) -> None:
        transport = QueueTransport([response(401, {"error": "do not persist this"})])

        with self.assertRaises(ProviderAuthenticationError):
            gemini_provider(transport, retries=2).generate(request())

        self.assertEqual(len(transport.calls), 1)

    def test_permanent_client_error_is_not_retried(self) -> None:
        transport = QueueTransport([response(400, {"error": "invalid request details"})])

        with self.assertRaises(ProviderPermanentError) as raised:
            gemini_provider(transport, retries=2).generate(request())

        self.assertNotIn("invalid request details", str(raised.exception))
        self.assertEqual(len(transport.calls), 1)

    def test_rate_limit_retries_with_bounded_budget(self) -> None:
        transport = QueueTransport(
            [
                response(429, {"error": "quota"}, headers={"Retry-After": "1000"}),
                response(200, gemini_response()),
            ]
        )

        result = gemini_provider(transport, retries=1).generate(request())

        self.assertEqual(result.usage.retry_count, 1)
        self.assertEqual(len(transport.calls), 2)

    def test_timeout_exhaustion_is_classified(self) -> None:
        transport = QueueTransport([ProviderTimeoutError("provider request timed out")])

        with self.assertRaises(ProviderTimeoutError):
            gemini_provider(transport).generate(request())

    def test_transient_server_error_falls_through_retry(self) -> None:
        transport = QueueTransport(
            [response(503, {"error": "temporary"}), response(200, gemini_response())]
        )

        result = gemini_provider(transport, retries=1).generate(request())

        self.assertEqual(result.usage.retry_count, 1)

    def test_malformed_outer_json_is_rejected_without_retry(self) -> None:
        transport = QueueTransport([response(200, b"not-json")])

        with self.assertRaises(ProviderResponseError):
            gemini_provider(transport, retries=2).generate(request())

        self.assertEqual(len(transport.calls), 1)

    def test_malformed_structured_output_is_rejected(self) -> None:
        transport = QueueTransport([response(200, gemini_response({"title": "Only"}))])

        with self.assertRaises(ProviderResponseError):
            gemini_provider(transport).generate(request())

    def test_unexpected_structured_field_is_rejected(self) -> None:
        malformed = {**master_payload(), "unrequested_private_state": "not allowed"}
        transport = QueueTransport([response(200, gemini_response(malformed))])

        with self.assertRaises(ProviderResponseError):
            gemini_provider(transport).generate(request())

    def test_usage_metadata_is_typed_and_free_cost_is_explicit(self) -> None:
        transport = QueueTransport([response(200, gemini_response())])

        usage = gemini_provider(transport).generate(request()).usage

        self.assertEqual(usage.input_tokens, 12)
        self.assertEqual(usage.output_tokens, 18)
        self.assertEqual(usage.total_tokens, 30)
        self.assertEqual(usage.estimated_cost, 0.0)
        self.assertEqual(usage.requested_at, NOW)


class OpenRouterProviderTests(unittest.TestCase):
    def test_successful_structured_response_and_request_contract(self) -> None:
        transport = QueueTransport([response(200, openrouter_response())])

        result = openrouter_provider(transport).generate(request())

        self.assertEqual(result.structured_output, master_payload())
        self.assertEqual(result.model, "openrouter/free-test")
        call = transport.calls[0]
        self.assertEqual(call["timeout_seconds"], 9)
        self.assertEqual(call["payload"]["response_format"]["type"], "json_schema")
        self.assertTrue(call["payload"]["response_format"]["json_schema"]["strict"])
        self.assertTrue(call["payload"]["provider"]["require_parameters"])

    def test_missing_credentials_is_not_configured(self) -> None:
        provider = OpenRouterProvider(OpenRouterProviderConfig(model="openrouter/free-test"))

        self.assertFalse(provider.is_configured)
        with self.assertRaises(ProviderConfigurationError):
            provider.generate(request())

    def test_authentication_failure_is_not_retried(self) -> None:
        transport = QueueTransport([response(403, {"error": "secret-bearing response"})])

        with self.assertRaises(ProviderAuthenticationError) as raised:
            openrouter_provider(transport, retries=2).generate(request())

        self.assertNotIn("secret-bearing", str(raised.exception))
        self.assertEqual(len(transport.calls), 1)

    def test_rate_limit_exhaustion_is_classified(self) -> None:
        transport = QueueTransport([response(429, {}), response(429, {}), response(429, {})])

        with self.assertRaises(ProviderRateLimitError) as raised:
            openrouter_provider(transport, retries=2).generate(request())

        self.assertEqual(len(transport.calls), 3)
        self.assertEqual(raised.exception.retry_count, 2)

    def test_timeout_then_success_uses_one_retry(self) -> None:
        transport = QueueTransport(
            [
                ProviderTimeoutError("provider request timed out"),
                response(200, openrouter_response()),
            ]
        )

        result = openrouter_provider(transport, retries=1).generate(request())

        self.assertEqual(result.usage.retry_count, 1)

    def test_transient_error_then_success(self) -> None:
        transport = QueueTransport([response(502, {}), response(200, openrouter_response())])

        result = openrouter_provider(transport, retries=1).generate(request())

        self.assertEqual(result.structured_output, master_payload())

    def test_malformed_response_shape_is_rejected(self) -> None:
        transport = QueueTransport([response(200, {"choices": []})])

        with self.assertRaises(ProviderResponseError):
            openrouter_provider(transport).generate(request())

    def test_wrong_schema_discriminator_is_rejected(self) -> None:
        malformed = {
            "platform": "facebook",
            "schema_version": "facebook_content_v1",
            "format": "single_post",
            "opening_hook": "Wrong discriminator",
            "posts": [{"order": 1, "text": "Wrong platform."}],
            "source_references": ["source-1"],
        }
        transport = QueueTransport([response(200, openrouter_response(malformed))])

        with self.assertRaises(ProviderResponseError):
            openrouter_provider(transport).generate(x_request())

    def test_fenced_json_is_trivially_normalized(self) -> None:
        payload = openrouter_response()
        payload["choices"][0]["message"]["content"] = (
            "```json\n" + json.dumps(master_payload()) + "\n```"
        )
        transport = QueueTransport([response(200, payload)])

        result = openrouter_provider(transport).generate(request())

        self.assertEqual(result.structured_output, master_payload())

    def test_usage_and_reported_cost_are_parsed(self) -> None:
        payload = openrouter_response(
            usage={
                "prompt_tokens": 5,
                "completion_tokens": 8,
                "total_tokens": 13,
                "cost": 0.002,
            }
        )
        transport = QueueTransport([response(200, payload)])

        usage = (
            openrouter_provider(transport, cost_class=ProviderCostClass.PAID)
            .generate(request())
            .usage
        )

        self.assertEqual(usage.input_tokens, 5)
        self.assertEqual(usage.output_tokens, 8)
        self.assertEqual(usage.total_tokens, 13)
        self.assertEqual(usage.estimated_cost, 0.002)


class ProductionRouterTests(unittest.TestCase):
    def test_gemini_failure_falls_back_to_openrouter(self) -> None:
        gemini_transport = QueueTransport([response(429, {})])
        openrouter_transport = QueueTransport([response(200, openrouter_response())])
        router = AIRouter(
            (
                gemini_provider(gemini_transport),
                openrouter_provider(openrouter_transport),
            ),
            provider_order=("gemini", "openrouter"),
        )

        result = router.generate(request())

        self.assertEqual(result.provider, "openrouter")
        self.assertEqual(len(gemini_transport.calls), 1)
        self.assertEqual(len(openrouter_transport.calls), 1)

    def test_preferred_provider_runs_before_configured_order(self) -> None:
        first = FakeAIProvider(provider_name="first")
        preferred = FakeAIProvider(provider_name="preferred")
        router = AIRouter((first, preferred), provider_order=("first", "preferred"))

        result = router.generate(request(), preferred_provider="preferred")

        self.assertEqual(result.provider, "preferred")
        self.assertEqual(first.requests, [])

    def test_unknown_cost_is_blocked_when_paid_ai_is_disabled(self) -> None:
        transport = QueueTransport([response(200, gemini_response())])
        provider = gemini_provider(transport, cost_class=ProviderCostClass.UNKNOWN)
        router = AIRouter((provider,), provider_order=("gemini",))

        with self.assertRaises(AIUnavailableError) as raised:
            router.generate(request())

        self.assertEqual(raised.exception.attempts[0].outcome, "paid_disabled")
        self.assertEqual(transport.calls, [])

    def test_missing_configuration_has_distinct_outcome(self) -> None:
        provider = GeminiProvider(GeminiProviderConfig(model="model-only"))
        router = AIRouter((provider,), provider_order=("gemini",))

        with self.assertRaises(AIUnavailableError) as raised:
            router.generate(request())

        self.assertEqual(raised.exception.attempts[0].outcome, "not_configured")

    def test_disabled_configured_provider_is_unavailable(self) -> None:
        provider = GeminiProvider(
            GeminiProviderConfig(
                api_key="test-secret",
                model="test-model",
                enabled=False,
                cost_class=ProviderCostClass.FREE,
            )
        )
        router = AIRouter((provider,), provider_order=("gemini",))

        with self.assertRaises(AIUnavailableError) as raised:
            router.generate(request())

        self.assertEqual(raised.exception.attempts[0].outcome, "unavailable")

    def test_exhausted_retry_count_is_exposed_to_router_trace(self) -> None:
        transport = QueueTransport([response(429, {}), response(429, {})])
        provider = gemini_provider(transport, retries=1)
        router = AIRouter((provider,), provider_order=("gemini",))

        with self.assertRaises(AIUnavailableError) as raised:
            router.generate(request())

        attempt = raised.exception.attempts[0]
        self.assertEqual(attempt.outcome, "rate_limited")
        self.assertEqual(attempt.retry_count, 1)


if __name__ == "__main__":
    unittest.main()
