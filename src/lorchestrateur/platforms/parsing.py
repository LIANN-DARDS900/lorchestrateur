"""Strict helpers shared by versioned platform output parsers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from lorchestrateur.ai.structured import StructuredOutputError


def require_contract(
    payload: Mapping[str, Any],
    *,
    platform: str,
    schema_version: str,
    required_fields: frozenset[str],
    optional_fields: frozenset[str] = frozenset(),
) -> None:
    if payload.get("platform") != platform:
        raise StructuredOutputError("platform_mismatch", f"platform must be {platform!r}")
    if payload.get("schema_version") != schema_version:
        raise StructuredOutputError(
            "schema_version_mismatch",
            f"schema_version must be {schema_version!r}",
        )
    allowed = required_fields | optional_fields | {"platform", "schema_version"}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise StructuredOutputError(
            "unsupported_field", f"unsupported fields: {', '.join(unknown)}"
        )
    missing = sorted(field for field in required_fields if field not in payload)
    if missing:
        raise StructuredOutputError(
            "invalid_required_field", f"missing fields: {', '.join(missing)}"
        )


def require_text(payload: Mapping[str, Any], field_name: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise StructuredOutputError(
            "invalid_required_field", f"{field_name} must be a non-empty string"
        )
    return value.strip()


def optional_text(payload: Mapping[str, Any], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise StructuredOutputError(
            "invalid_optional_field", f"{field_name} must be a string or null"
        )
    return value.strip() or None


def require_sequence(payload: Mapping[str, Any], field_name: str) -> Sequence[Any]:
    value = payload.get(field_name)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise StructuredOutputError("invalid_required_field", f"{field_name} must be an array")
    if not value:
        raise StructuredOutputError("empty_required_collection", f"{field_name} cannot be empty")
    return value


def parse_string_sequence(
    value: Any,
    field_name: str,
    *,
    required: bool,
) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise StructuredOutputError(
            "invalid_required_field", f"{field_name} must be an array of strings"
        )
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise StructuredOutputError(
                "invalid_required_field",
                f"{field_name} must contain non-empty strings",
            )
        items.append(item.strip())
    if required and not items:
        raise StructuredOutputError("empty_required_collection", f"{field_name} cannot be empty")
    if len(items) != len(set(items)):
        raise StructuredOutputError(
            "duplicate_identifiers", f"{field_name} contains duplicate values"
        )
    return tuple(items)


def parse_source_references(payload: Mapping[str, Any]) -> tuple[str, ...]:
    return parse_string_sequence(
        payload.get("source_references"), "source_references", required=True
    )


def require_order(value: Any, field_name: str = "order") -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise StructuredOutputError(
            "invalid_item_order", f"{field_name} must be a positive integer"
        )
    return value
