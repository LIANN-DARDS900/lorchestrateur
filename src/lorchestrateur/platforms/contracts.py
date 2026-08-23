"""Extensible platform definition and deterministic schema validation."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from lorchestrateur.domain.validation import ValidationIssue, ValidationResult


@dataclass(frozen=True, slots=True)
class ContentFieldRule:
    required: bool = True
    min_length: int | None = None
    max_length: int | None = None

    def __post_init__(self) -> None:
        if self.min_length is not None and self.min_length < 0:
            raise ValueError("min_length cannot be negative")
        if self.max_length is not None and self.max_length <= 0:
            raise ValueError("max_length must be positive")
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("min_length cannot exceed max_length")


@dataclass(frozen=True, slots=True)
class PlatformContent:
    platform: str
    fields: Mapping[str, str]


class Platform(Protocol):
    """Extension point for platform schema, guidance, and validation behavior."""

    @property
    def key(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def adaptation_guidance(self) -> str: ...

    def validate(self, content: PlatformContent) -> ValidationResult: ...


@dataclass(frozen=True, slots=True)
class SchemaPlatform:
    """Declarative platform implementation for deterministic field constraints."""

    key: str
    display_name: str
    adaptation_guidance: str
    field_rules: Mapping[str, ContentFieldRule]

    def __post_init__(self) -> None:
        if not self.key.strip():
            raise ValueError("platform key cannot be empty")
        if not self.field_rules:
            raise ValueError("platform must define at least one content field")

    def validate(self, content: PlatformContent) -> ValidationResult:
        issues: list[ValidationIssue] = []
        if content.platform != self.key:
            issues.append(
                ValidationIssue(
                    code="platform_mismatch",
                    message=f"content targets {content.platform!r}, expected {self.key!r}",
                )
            )

        unknown_fields = sorted(set(content.fields) - set(self.field_rules))
        for field_name in unknown_fields:
            issues.append(
                ValidationIssue(
                    code="unsupported_field",
                    field=field_name,
                    message=f"field {field_name!r} is not defined for {self.display_name}",
                )
            )

        for field_name, rule in self.field_rules.items():
            value = content.fields.get(field_name, "")
            if rule.required and not value.strip():
                issues.append(
                    ValidationIssue(
                        code="required",
                        field=field_name,
                        message=f"{field_name} is required",
                    )
                )
                continue
            if not value:
                continue
            if rule.min_length is not None and len(value) < rule.min_length:
                issues.append(
                    ValidationIssue(
                        code="min_length",
                        field=field_name,
                        message=f"{field_name} must contain at least {rule.min_length} characters",
                    )
                )
            if rule.max_length is not None and len(value) > rule.max_length:
                issues.append(
                    ValidationIssue(
                        code="max_length",
                        field=field_name,
                        message=f"{field_name} exceeds {rule.max_length} characters",
                    )
                )

        return ValidationResult(tuple(issues))

