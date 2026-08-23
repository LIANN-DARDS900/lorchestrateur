"""Provider-independent AI contracts, routing, and test doubles."""

from lorchestrateur.ai.contracts import (
    AIOutputSchema,
    AIProvider,
    AIProviderError,
    AIRequest,
    AIResponse,
    AITask,
    AIUsage,
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderCostClass,
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransientError,
)
from lorchestrateur.ai.fake import FakeAIProvider
from lorchestrateur.ai.router import AIRouter, AIUnavailableError
from lorchestrateur.ai.structured import (
    ContentStrategyOutput,
    MasterContentOutput,
    StructuredOutputError,
)

__all__ = [
    "AIProvider",
    "AIProviderError",
    "AIOutputSchema",
    "AIRequest",
    "AIResponse",
    "AIUsage",
    "AIRouter",
    "AITask",
    "AIUnavailableError",
    "ContentStrategyOutput",
    "FakeAIProvider",
    "MasterContentOutput",
    "ProviderAuthenticationError",
    "ProviderConfigurationError",
    "ProviderCostClass",
    "ProviderPermanentError",
    "ProviderRateLimitError",
    "ProviderResponseError",
    "ProviderTimeoutError",
    "ProviderTransientError",
    "StructuredOutputError",
]
