"""Deterministic AI test double; never calls an external service."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from lorchestrateur.ai.contracts import AIProviderError, AIRequest, AIResponse


@dataclass(slots=True)
class FakeAIProvider:
    response_content: str = "Deterministic fake response"
    provider_name: str = "fake"
    model_name: str = "fake-v1"
    paid: bool = False
    configured: bool = True
    available: bool = True
    handler: Callable[[AIRequest], str] | None = None
    structured_output: Mapping[str, Any] | None = None
    structured_handler: Callable[[AIRequest], Mapping[str, Any] | None] | None = None
    failure: AIProviderError | None = None
    requests: list[AIRequest] = field(default_factory=list, init=False)

    @property
    def name(self) -> str:
        return self.provider_name

    @property
    def is_configured(self) -> bool:
        return self.configured

    @property
    def is_paid(self) -> bool:
        return self.paid

    def is_available(self) -> bool:
        return self.available

    def generate(self, request: AIRequest) -> AIResponse:
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure
        content = self.handler(request) if self.handler else self.response_content
        structured_output = (
            self.structured_handler(request)
            if self.structured_handler is not None
            else self.structured_output
        )
        return AIResponse(
            content=content[: request.max_output_characters],
            provider=self.name,
            model=self.model_name,
            metadata={"fake": True},
            structured_output=structured_output,
        )
