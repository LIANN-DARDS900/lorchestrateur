"""Composition helpers for governed production providers and the central router."""

from __future__ import annotations

from collections.abc import Iterable

from lorchestrateur.ai.contracts import AIProvider
from lorchestrateur.ai.providers.gemini import GeminiProvider, GeminiProviderConfig
from lorchestrateur.ai.providers.openrouter import OpenRouterProvider, OpenRouterProviderConfig
from lorchestrateur.ai.router import AIRouter
from lorchestrateur.config import Settings


def create_production_providers(settings: Settings) -> tuple[AIProvider, ...]:
    return (
        GeminiProvider(
            GeminiProviderConfig(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                base_url=settings.gemini_base_url,
                timeout_seconds=settings.gemini_timeout_seconds,
                max_retries=settings.gemini_max_retries,
                cost_class=settings.gemini_cost_class,
                enabled=settings.gemini_enabled,
            )
        ),
        OpenRouterProvider(
            OpenRouterProviderConfig(
                api_key=settings.openrouter_api_key,
                model=settings.openrouter_model,
                base_url=settings.openrouter_base_url,
                timeout_seconds=settings.openrouter_timeout_seconds,
                max_retries=settings.openrouter_max_retries,
                cost_class=settings.openrouter_cost_class,
                enabled=settings.openrouter_enabled,
            )
        ),
    )


def create_ai_router(
    settings: Settings, *, additional_providers: Iterable[AIProvider] = ()
) -> AIRouter:
    providers = (*tuple(additional_providers), *create_production_providers(settings))
    return AIRouter(
        providers,
        provider_order=settings.ai_provider_order,
        allow_paid_ai=settings.allow_paid_ai,
    )
