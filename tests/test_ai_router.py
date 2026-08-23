import unittest

from lorchestrateur.ai.contracts import AIProviderError, AIRequest, AITask
from lorchestrateur.ai.fake import FakeAIProvider
from lorchestrateur.ai.router import AIRouter, AIUnavailableError


def request() -> AIRequest:
    return AIRequest(task=AITask.MASTER_CONTENT, prompt="Create master content", context={})


class AvailabilityFailureProvider(FakeAIProvider):
    def is_available(self) -> bool:
        raise AIProviderError("health check failed")


class AIRouterTests(unittest.TestCase):
    def test_routes_to_first_available_free_provider(self) -> None:
        unavailable = FakeAIProvider(provider_name="local", available=False)
        free = FakeAIProvider(provider_name="community", response_content="result")
        router = AIRouter(
            (unavailable, free),
            provider_order=("local", "community"),
        )

        response = router.generate(request())

        self.assertEqual(response.provider, "community")
        self.assertEqual(len(free.requests), 1)

    def test_paid_provider_is_never_called_by_default(self) -> None:
        paid = FakeAIProvider(provider_name="paid", paid=True)
        router = AIRouter((paid,), provider_order=("paid",))

        with self.assertRaises(AIUnavailableError) as raised:
            router.generate(request())

        self.assertEqual(paid.requests, [])
        self.assertEqual(raised.exception.attempts[0].outcome, "paid_disabled")

    def test_paid_provider_requires_explicit_opt_in(self) -> None:
        paid = FakeAIProvider(provider_name="paid", paid=True)
        router = AIRouter((paid,), provider_order=("paid",), allow_paid_ai=True)

        response = router.generate(request())

        self.assertEqual(response.provider, "paid")

    def test_expected_provider_error_falls_back(self) -> None:
        failing = FakeAIProvider(
            provider_name="primary", failure=AIProviderError("temporary failure")
        )
        fallback = FakeAIProvider(provider_name="fallback")
        router = AIRouter(
            (failing, fallback), provider_order=("primary", "fallback")
        )

        response = router.generate(request())

        self.assertEqual(response.provider, "fallback")

    def test_availability_error_falls_back(self) -> None:
        unhealthy = AvailabilityFailureProvider(provider_name="unhealthy")
        fallback = FakeAIProvider(provider_name="fallback")
        router = AIRouter(
            (unhealthy, fallback), provider_order=("unhealthy", "fallback")
        )

        response = router.generate(request())

        self.assertEqual(response.provider, "fallback")


if __name__ == "__main__":
    unittest.main()
