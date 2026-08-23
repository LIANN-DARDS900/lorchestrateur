"""Provider-independent request serialization and strict structured response parsing."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from lorchestrateur.ai.contracts import (
    AIRequest,
    ProviderCostClass,
    ProviderPermanentError,
    ProviderResponseError,
)
from lorchestrateur.ai.schemas import response_schema_for


@dataclass(frozen=True, slots=True)
class ProviderEndpointConfig:
    api_key: str | None = field(default=None, repr=False)
    model: str = ""
    base_url: str = ""
    timeout_seconds: float = 30.0
    max_retries: int = 2
    cost_class: ProviderCostClass = ProviderCostClass.UNKNOWN
    enabled: bool = True

    def __post_init__(self) -> None:
        api_key = self.api_key.strip() if self.api_key else None
        model = self.model.strip()
        base_url = self.base_url.strip().rstrip("/")
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "model", model)
        object.__setattr__(self, "base_url", base_url)
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError("provider base_url must be an HTTPS URL without query or fragment")
        if not isinstance(self.timeout_seconds, (int, float)) or isinstance(
            self.timeout_seconds, bool
        ):
            raise ValueError("provider timeout_seconds must be numeric")
        if not 0 < self.timeout_seconds <= 300:
            raise ValueError("provider timeout_seconds must be between 0 and 300")
        if not isinstance(self.max_retries, int) or isinstance(self.max_retries, bool):
            raise ValueError("provider max_retries must be an integer")
        if not 0 <= self.max_retries <= 5:
            raise ValueError("provider max_retries must be between 0 and 5")
        if not isinstance(self.cost_class, ProviderCostClass):
            raise ValueError("provider cost_class must be a ProviderCostClass")
        if not isinstance(self.enabled, bool):
            raise ValueError("provider enabled must be a boolean")


def request_schema(request: AIRequest) -> dict[str, Any]:
    if request.output_schema is None:
        raise ProviderPermanentError("production providers require an output schema")
    if request.response_json_schema is not None:
        return _json_value(request.response_json_schema)
    return response_schema_for(request.output_schema)


def request_text(request: AIRequest) -> str:
    """Serialize only the explicit AI request contract; never arbitrary domain objects."""

    if request.output_schema is None:
        raise ProviderPermanentError("production providers require an output schema")
    payload = {
        "task": request.task.value,
        "output_schema": request.output_schema.value,
        "maximum_output_characters": request.max_output_characters,
        "instructions": request.prompt,
        "context": _json_value(request.context),
    }
    try:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ProviderPermanentError("AI request contains non-JSON context") from exc


def maximum_output_tokens(request: AIRequest) -> int:
    return max(1, math.ceil(request.max_output_characters / 4))


def parse_structured_text(raw_text: str, request: AIRequest) -> tuple[str, Mapping[str, Any]]:
    if not isinstance(raw_text, str) or not raw_text.strip():
        raise ProviderResponseError("provider returned empty structured content")
    normalized = _strip_single_json_fence(raw_text)
    if len(normalized) > request.max_output_characters:
        raise ProviderResponseError("provider output exceeded the requested character limit")
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError as exc:
        raise ProviderResponseError("provider returned invalid structured JSON") from exc
    if not isinstance(payload, Mapping):
        raise ProviderResponseError("provider structured output must be a JSON object")
    _validate_schema_value(payload, request_schema(request))
    return normalized, payload


def _strip_single_json_fence(raw_text: str) -> str:
    normalized = raw_text.strip()
    if not normalized.startswith("```"):
        return normalized
    lines = normalized.splitlines()
    if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"}:
        raise ProviderResponseError("provider returned unsupported text around structured JSON")
    if lines[-1].strip() != "```":
        raise ProviderResponseError("provider returned an incomplete JSON fence")
    inner = "\n".join(lines[1:-1]).strip()
    if "```" in inner:
        raise ProviderResponseError("provider returned nested structured-output fences")
    return inner


def _json_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 30:
        raise ProviderPermanentError("AI request context is nested too deeply")
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ProviderPermanentError("AI request context contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProviderPermanentError("AI request context keys must be strings")
            result[key] = _json_value(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item, depth=depth + 1) for item in value]
    raise ProviderPermanentError("AI request context contains an unsupported value")


def _validate_schema_value(value: Any, schema: Mapping[str, Any]) -> None:
    expected_type = schema.get("type")
    valid_type = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected_type)
    if valid_type is not True:
        raise ProviderResponseError("provider output does not match the requested schema")
    if "enum" in schema and value not in schema["enum"]:
        raise ProviderResponseError("provider output contains an invalid schema discriminator")
    if expected_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", ())
        if any(key not in value for key in required):
            raise ProviderResponseError("provider output is missing required schema fields")
        if schema.get("additionalProperties") is False and any(
            key not in properties for key in value
        ):
            raise ProviderResponseError("provider output contains unexpected schema fields")
        for key, item in value.items():
            child_schema = properties.get(key)
            if child_schema is not None:
                _validate_schema_value(item, child_schema)
    elif expected_type == "array":
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if minimum is not None and len(value) < minimum:
            raise ProviderResponseError("provider output contains too few array items")
        if maximum is not None and len(value) > maximum:
            raise ProviderResponseError("provider output contains too many array items")
        item_schema = schema.get("items")
        if item_schema is not None:
            for item in value:
                _validate_schema_value(item, item_schema)
