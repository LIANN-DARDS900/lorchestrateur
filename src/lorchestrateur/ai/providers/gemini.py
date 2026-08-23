"""Gemini GenerateContent adapter behind the provider-independent AI contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from time import sleep
from typing import Any
from urllib.parse import quote

from lorchestrateur.ai.contracts import (
    AIRequest,
    AIResponse,
    AIUsage,
    ProviderConfigurationError,
    ProviderCostClass,
    ProviderResponseError,
)
from lorchestrateur.ai.providers.common import (
    ProviderEndpointConfig,
    maximum_output_tokens,
    parse_structured_text,
    request_schema,
    request_text,
)
from lorchestrateur.ai.providers.http import (
    GovernedHTTPClient,
    JSONTransport,
    RetryPolicy,
)


@dataclass(frozen=True, slots=True)
class GeminiProviderConfig(ProviderEndpointConfig):
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"


class GeminiProvider:
    name = "gemini"

    def __init__(
        self,
        config: GeminiProviderConfig,
        *,
        transport: JSONTransport | None = None,
        sleeper: Callable[[float], None] = sleep,
        timer: Callable[[], float] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._config = config
        client_kwargs: dict[str, Any] = {
            "transport": transport,
            "retry_policy": RetryPolicy(max_retries=config.max_retries),
            "sleeper": sleeper,
        }
        if timer is not None:
            client_kwargs["timer"] = timer
        if clock is not None:
            client_kwargs["clock"] = clock
        self._client = GovernedHTTPClient(**client_kwargs)

    @property
    def model(self) -> str:
        return self._config.model

    @property
    def cost_class(self) -> ProviderCostClass:
        return self._config.cost_class

    @property
    def is_configured(self) -> bool:
        return bool(self._config.api_key and self._config.model)

    @property
    def is_paid(self) -> bool:
        return self.cost_class.requires_paid_authorization

    def is_available(self) -> bool:
        return self.is_configured and self._config.enabled

    def generate(self, request: AIRequest) -> AIResponse:
        if not self.is_configured:
            raise ProviderConfigurationError("Gemini credentials and model are required")
        if not self._config.enabled:
            raise ProviderConfigurationError("Gemini provider is disabled")
        model_path = quote(self._config.model.removeprefix("models/"), safe="")
        execution = self._client.post_json(
            self.name,
            f"{self._config.base_url}/models/{model_path}:generateContent",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self._config.api_key or "",
            },
            payload={
                "contents": [{"role": "user", "parts": [{"text": request_text(request)}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "responseJsonSchema": request_schema(request),
                    "maxOutputTokens": maximum_output_tokens(request),
                    "temperature": 0.2,
                },
            },
            timeout_seconds=self._config.timeout_seconds,
        )
        raw_text, structured = parse_structured_text(_gemini_text(execution.payload), request)
        usage_payload = execution.payload.get("usageMetadata")
        usage_mapping = usage_payload if isinstance(usage_payload, Mapping) else {}
        usage = AIUsage(
            requested_at=execution.requested_at,
            latency_ms=execution.latency_ms,
            retry_count=execution.retry_count,
            input_tokens=_optional_count(usage_mapping.get("promptTokenCount")),
            output_tokens=_optional_count(usage_mapping.get("candidatesTokenCount")),
            total_tokens=_optional_count(usage_mapping.get("totalTokenCount")),
            estimated_cost=(0.0 if self.cost_class is ProviderCostClass.FREE else None),
            cost_class=self.cost_class,
        )
        finish_reason = _gemini_finish_reason(execution.payload)
        return AIResponse(
            content=raw_text,
            provider=self.name,
            model=self._config.model,
            metadata={
                "finish_reason": finish_reason,
                "retry_count": execution.retry_count,
            },
            structured_output=structured,
            usage=usage,
        )


def _gemini_text(payload: Mapping[str, Any]) -> str:
    candidates = payload.get("candidates")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise ProviderResponseError("Gemini response contains no candidates")
    if not candidates or not isinstance(candidates[0], Mapping):
        raise ProviderResponseError("Gemini response contains no usable candidate")
    content = candidates[0].get("content")
    if not isinstance(content, Mapping):
        raise ProviderResponseError("Gemini response candidate contains no content")
    parts = content.get("parts")
    if not isinstance(parts, Sequence) or isinstance(parts, (str, bytes)) or not parts:
        raise ProviderResponseError("Gemini response candidate contains no text")
    text_parts = [part.get("text") for part in parts if isinstance(part, Mapping)]
    text = "".join(item for item in text_parts if isinstance(item, str))
    if not text.strip():
        raise ProviderResponseError("Gemini response candidate contains empty text")
    return text


def _gemini_finish_reason(payload: Mapping[str, Any]) -> str | None:
    candidates = payload.get("candidates")
    if (
        isinstance(candidates, Sequence)
        and not isinstance(candidates, (str, bytes))
        and candidates
        and isinstance(candidates[0], Mapping)
    ):
        value = candidates[0].get("finishReason")
        return value if isinstance(value, str) else None
    return None


def _optional_count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
