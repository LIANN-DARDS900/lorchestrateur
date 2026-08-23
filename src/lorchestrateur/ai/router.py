"""Central AI provider selection with explicit paid-provider governance."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from lorchestrateur.ai.contracts import AIProvider, AIProviderError, AIRequest, AIResponse


class ProviderRegistrationError(ValueError):
    """Raised for an invalid or duplicate provider registration."""


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    provider: str
    outcome: str
    retry_count: int = 0


class AIUnavailableError(RuntimeError):
    """Raised when routing cannot use any eligible provider."""

    def __init__(self, attempts: Sequence[ProviderAttempt]) -> None:
        self.attempts = tuple(attempts)
        summary = ", ".join(f"{item.provider}:{item.outcome}" for item in attempts)
        super().__init__(f"no eligible AI provider is available ({summary or 'none registered'})")


class AIRouter:
    """Selects providers deterministically and never bypasses paid-AI policy."""

    def __init__(
        self,
        providers: Iterable[AIProvider],
        *,
        provider_order: Sequence[str],
        allow_paid_ai: bool = False,
    ) -> None:
        self._providers: dict[str, AIProvider] = {}
        for provider in providers:
            if not provider.name.strip():
                raise ProviderRegistrationError("provider name cannot be empty")
            if provider.name in self._providers:
                raise ProviderRegistrationError(f"provider already registered: {provider.name}")
            self._providers[provider.name] = provider
        self._provider_order = tuple(dict.fromkeys(provider_order))
        self._allow_paid_ai = allow_paid_ai

    def generate(
        self,
        request: AIRequest,
        *,
        preferred_provider: str | None = None,
    ) -> AIResponse:
        order = self._ordered_candidates(preferred_provider)
        attempts: list[ProviderAttempt] = []

        for provider_name in order:
            provider = self._providers.get(provider_name)
            if provider is None:
                attempts.append(ProviderAttempt(provider_name, "not_registered"))
                continue
            if not provider.is_configured:
                attempts.append(ProviderAttempt(provider_name, "not_configured"))
                continue
            if provider.is_paid and not self._allow_paid_ai:
                attempts.append(ProviderAttempt(provider_name, "paid_disabled"))
                continue
            try:
                available = provider.is_available()
            except AIProviderError as exc:
                outcome = (
                    "availability_error"
                    if exc.classification == "provider_error"
                    else exc.classification
                )
                attempts.append(ProviderAttempt(provider_name, outcome))
                continue
            if not available:
                attempts.append(ProviderAttempt(provider_name, "unavailable"))
                continue
            try:
                return provider.generate(request)
            except AIProviderError as exc:
                attempts.append(
                    ProviderAttempt(provider_name, exc.classification, exc.retry_count)
                )

        raise AIUnavailableError(attempts)

    def _ordered_candidates(self, preferred_provider: str | None) -> tuple[str, ...]:
        if preferred_provider is None:
            return self._provider_order
        return tuple(
            dict.fromkeys((preferred_provider, *self._provider_order))
        )
