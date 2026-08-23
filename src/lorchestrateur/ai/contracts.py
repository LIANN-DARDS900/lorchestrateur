"""Contracts shared by AI provider adapters and the orchestration layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol


class AITask(StrEnum):
    STRATEGIC_ANGLE = "strategic_angle"
    CONTENT_STRATEGY = "content_strategy"
    MASTER_CONTENT = "master_content"
    PLATFORM_ADAPTATION = "platform_adaptation"
    CONTROLLED_REWRITE = "controlled_rewrite"


class AIOutputSchema(StrEnum):
    CONTENT_STRATEGY_V1 = "content_strategy_v1"
    MASTER_CONTENT_V1 = "master_content_v1"
    BLOG_CONTENT_V1 = "blog_content_v1"
    X_CONTENT_V1 = "x_content_v1"
    INSTAGRAM_CONTENT_V1 = "instagram_content_v1"
    FACEBOOK_CONTENT_V1 = "facebook_content_v1"


@dataclass(frozen=True, slots=True)
class AIRequest:
    task: AITask
    prompt: str
    context: Mapping[str, Any]
    max_output_characters: int = 10_000
    output_schema: AIOutputSchema | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task, AITask):
            raise ValueError("AI request task must be an AITask")
        if not isinstance(self.prompt, str) or not self.prompt.strip():
            raise ValueError("AI request prompt cannot be empty")
        if not isinstance(self.context, Mapping):
            raise ValueError("AI request context must be a mapping")
        if not isinstance(self.max_output_characters, int) or isinstance(
            self.max_output_characters, bool
        ):
            raise ValueError("max_output_characters must be an integer")
        if self.max_output_characters <= 0:
            raise ValueError("max_output_characters must be positive")
        if self.output_schema is not None and not isinstance(
            self.output_schema, AIOutputSchema
        ):
            raise ValueError("output_schema must be an AIOutputSchema")
        object.__setattr__(self, "context", MappingProxyType(dict(self.context)))


@dataclass(frozen=True, slots=True)
class AIResponse:
    content: str
    provider: str
    model: str
    metadata: Mapping[str, Any]
    structured_output: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise ValueError("AI response content must be a string")
        if not isinstance(self.provider, str) or not self.provider.strip():
            raise ValueError("AI response provider cannot be empty")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("AI response model cannot be empty")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("AI response metadata must be a mapping")
        if self.structured_output is not None and not isinstance(
            self.structured_output, Mapping
        ):
            raise ValueError("structured_output must be a mapping when provided")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))
        if self.structured_output is not None:
            object.__setattr__(
                self,
                "structured_output",
                MappingProxyType(dict(self.structured_output)),
            )


class AIProviderError(RuntimeError):
    """Expected provider failure that permits routing to another provider."""


class AIProvider(Protocol):
    """Minimal interface implemented by local, hosted, and test providers."""

    @property
    def name(self) -> str: ...

    @property
    def is_paid(self) -> bool: ...

    def is_available(self) -> bool: ...

    def generate(self, request: AIRequest) -> AIResponse: ...
