"""Provider-independent AI contracts, routing, and test doubles."""

from lorchestrateur.ai.contracts import (
    AIProvider,
    AIProviderError,
    AIOutputSchema,
    AIRequest,
    AIResponse,
    AITask,
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
    "AIRouter",
    "AITask",
    "AIUnavailableError",
    "ContentStrategyOutput",
    "FakeAIProvider",
    "MasterContentOutput",
    "StructuredOutputError",
]
