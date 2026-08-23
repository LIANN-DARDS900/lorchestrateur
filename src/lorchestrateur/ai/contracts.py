"""Contracts shared by AI provider adapters and the orchestration layer."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class AITask(StrEnum):
    STRATEGIC_ANGLE = "strategic_angle"
    MASTER_CONTENT = "master_content"
    PLATFORM_ADAPTATION = "platform_adaptation"
    CONTROLLED_REWRITE = "controlled_rewrite"


@dataclass(frozen=True, slots=True)
class AIRequest:
    task: AITask
    prompt: str
    context: Mapping[str, Any]
    max_output_characters: int = 10_000

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("AI request prompt cannot be empty")
        if self.max_output_characters <= 0:
            raise ValueError("max_output_characters must be positive")


@dataclass(frozen=True, slots=True)
class AIResponse:
    content: str
    provider: str
    model: str
    metadata: Mapping[str, Any]


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

