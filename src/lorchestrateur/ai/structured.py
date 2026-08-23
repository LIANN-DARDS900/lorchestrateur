"""Typed, versioned schemas for structured AI generation outputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class StructuredOutputError(ValueError):
    """Raised when provider output does not satisfy the requested schema."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _require_exact_fields(
    payload: Mapping[str, Any], *, required: set[str], optional: set[str] | None = None
) -> None:
    actual = set(payload)
    missing = required - actual
    if missing:
        raise StructuredOutputError(
            "invalid_required_field",
            f"structured output is missing fields: {', '.join(sorted(missing))}",
        )
    unexpected = actual - required - (optional or set())
    if unexpected:
        raise StructuredOutputError(
            "unexpected_fields",
            f"structured output contains unexpected fields: {', '.join(sorted(unexpected))}",
        )


def _require_text(payload: Mapping[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise StructuredOutputError(
            "invalid_required_field", f"{field_name} must be a non-empty string"
        )
    return value.strip()


def _require_sequence(payload: Mapping[str, Any], field_name: str) -> Sequence[Any]:
    value = payload.get(field_name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise StructuredOutputError(
            "invalid_required_field", f"{field_name} must be an array"
        )
    if not value:
        raise StructuredOutputError(
            "empty_required_collection", f"{field_name} cannot be empty"
        )
    return value


def _parse_string_ids(value: Any, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise StructuredOutputError(
            "invalid_source_references", f"{field_name} must be an array of source IDs"
        )
    source_ids: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise StructuredOutputError(
                "invalid_source_references",
                f"{field_name} must contain only non-empty strings",
            )
        source_ids.append(item.strip())
    if not source_ids:
        raise StructuredOutputError(
            "empty_source_references", f"{field_name} cannot be empty"
        )
    if len(source_ids) != len(set(source_ids)):
        raise StructuredOutputError(
            "duplicate_source_references", f"{field_name} contains duplicate IDs"
        )
    return tuple(source_ids)


@dataclass(frozen=True, slots=True)
class StrategyKeyMessageOutput:
    message: str
    source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContentStrategyOutput:
    objective: str
    target_audience: str
    angle: str
    tone: str
    key_messages: tuple[StrategyKeyMessageOutput, ...]
    intended_outcome: str

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ContentStrategyOutput:
        _require_exact_fields(
            payload,
            required={
                "objective",
                "target_audience",
                "angle",
                "tone",
                "key_messages",
                "intended_outcome",
            },
        )
        key_messages: list[StrategyKeyMessageOutput] = []
        for item in _require_sequence(payload, "key_messages"):
            if not isinstance(item, Mapping):
                raise StructuredOutputError(
                    "invalid_key_message", "each key message must be an object"
                )
            _require_exact_fields(item, required={"message", "source_ids"})
            key_messages.append(
                StrategyKeyMessageOutput(
                    message=_require_text(item, "message"),
                    source_ids=_parse_string_ids(item.get("source_ids"), "source_ids"),
                )
            )
        messages = tuple(item.message for item in key_messages)
        if len(messages) != len(set(messages)):
            raise StructuredOutputError(
                "duplicate_key_messages", "key_messages contains duplicate messages"
            )
        return cls(
            objective=_require_text(payload, "objective"),
            target_audience=_require_text(payload, "target_audience"),
            angle=_require_text(payload, "angle"),
            tone=_require_text(payload, "tone"),
            key_messages=tuple(key_messages),
            intended_outcome=_require_text(payload, "intended_outcome"),
        )


@dataclass(frozen=True, slots=True)
class MasterContentOutput:
    title: str
    summary: str
    body: str
    key_points: tuple[str, ...]
    source_ids: tuple[str, ...]

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> MasterContentOutput:
        _require_exact_fields(
            payload,
            required={"title", "summary", "body", "key_points", "source_ids"},
        )
        key_points: list[str] = []
        for item in _require_sequence(payload, "key_points"):
            if not isinstance(item, str) or not item.strip():
                raise StructuredOutputError(
                    "invalid_key_points", "key_points must contain non-empty strings"
                )
            key_points.append(item.strip())
        if len(key_points) != len(set(key_points)):
            raise StructuredOutputError(
                "duplicate_key_points", "key_points contains duplicate values"
            )
        return cls(
            title=_require_text(payload, "title"),
            summary=_require_text(payload, "summary"),
            body=_require_text(payload, "body"),
            key_points=tuple(key_points),
            source_ids=_parse_string_ids(payload.get("source_ids"), "source_ids"),
        )
