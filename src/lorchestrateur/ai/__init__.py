"""Provider-independent AI contracts, routing, and test doubles."""

from lorchestrateur.ai.contracts import (
    AIProvider,
    AIProviderError,
    AIRequest,
    AIResponse,
    AITask,
)
from lorchestrateur.ai.fake import FakeAIProvider
from lorchestrateur.ai.router import AIRouter, AIUnavailableError

__all__ = [
    "AIProvider",
    "AIProviderError",
    "AIRequest",
    "AIResponse",
    "AIRouter",
    "AITask",
    "AIUnavailableError",
    "FakeAIProvider",
]

