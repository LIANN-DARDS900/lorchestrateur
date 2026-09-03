"""OpenRouter chat-completions adapter behind the provider-independent AI contract."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from time import sleep
from typing import Any

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
class OpenRouterProviderConfig(ProviderEndpointConfig):
    base_url: str = "https://openrouter.ai/api/v1"


class OpenRouterProvider:
    name = "openrouter"

    def __init__(
        self,
        config: OpenRouterProviderConfig,
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
            raise ProviderConfigurationError("OpenRouter credentials and model are required")
        if not self._config.enabled:
            raise ProviderConfigurationError("OpenRouter provider is disabled")
        execution = self._client.post_json(
            self.name,
            f"{self._config.base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self._config.api_key or ''}",
                "Content-Type": "application/json",
            },
            payload={
                "model": self._config.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "Return only the requested JSON object. Do not include markdown, "
                            "commentary, or unsupported factual claims."
                        ),
                    },
                    {"role": "user", "content": request_text(request)},
                ],
                "max_tokens": maximum_output_tokens(request),
                "temperature": 0.2,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.output_schema.value if request.output_schema else "output",
                        "strict": True,
                        "schema": request_schema(request),
                    },
                },
                "provider": {"require_parameters": True},
            },
            timeout_seconds=self._config.timeout_seconds,
        )
        raw_text, structured = parse_structured_text(_openrouter_text(execution.payload), request)
        usage_payload = execution.payload.get("usage")
        usage_mapping = usage_payload if isinstance(usage_payload, Mapping) else {}
        reported_cost = _optional_cost(usage_mapping.get("cost"))
        estimated_cost = 0.0 if self.cost_class is ProviderCostClass.FREE else reported_cost
        usage = AIUsage(
            requested_at=execution.requested_at,
            latency_ms=execution.latency_ms,
            retry_count=execution.retry_count,
            input_tokens=_optional_count(usage_mapping.get("prompt_tokens")),
            output_tokens=_optional_count(usage_mapping.get("completion_tokens")),
            total_tokens=_optional_count(usage_mapping.get("total_tokens")),
            estimated_cost=estimated_cost,
            cost_class=self.cost_class,
        )
        return AIResponse(
            content=raw_text,
            provider=self.name,
            model=_response_model(execution.payload, self._config.model),
            metadata={
                "finish_reason": _finish_reason(execution.payload),
                "retry_count": execution.retry_count,
            },
            structured_output=structured,
            usage=usage,
        )


def _first_choice(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    choices = payload.get("choices")
    if not isinstance(choices, Sequence) or isinstance(choices, (str, bytes)):
        raise ProviderResponseError("OpenRouter response contains no choices")
    if not choices or not isinstance(choices[0], Mapping):
        raise ProviderResponseError("OpenRouter response contains no usable choice")
    return choices[0]


def _openrouter_text(payload: Mapping[str, Any]) -> str:
    message = _first_choice(payload).get("message")
    if not isinstance(message, Mapping):
        raise ProviderResponseError("OpenRouter response choice contains no message")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ProviderResponseError("OpenRouter response message contains no text")
    return content


def _finish_reason(payload: Mapping[str, Any]) -> str | None:
    value = _first_choice(payload).get("finish_reason")
    return value if isinstance(value, str) else None


def _response_model(payload: Mapping[str, Any], configured_model: str) -> str:
    value = payload.get("model")
    return value.strip() if isinstance(value, str) and value.strip() else configured_model


def _optional_count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _optional_cost(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None
