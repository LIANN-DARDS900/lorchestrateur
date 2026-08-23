"""Extensible platform contracts and shared deterministic governance helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from lorchestrateur.ai.contracts import AIOutputSchema, AIRequest
from lorchestrateur.domain.content import EvidenceStatus, MasterContent, SourceEvidence
from lorchestrateur.domain.platform_content import (
    PlatformContentRecord,
    PlatformPayload,
    QualityBreakdown,
    QualityPolicy,
)
from lorchestrateur.domain.validation import ValidationIssue, ValidationResult
from lorchestrateur.domain.workflow import ContentJob


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
    """Phase 1 compatibility input for declarative string-field validation."""

    platform: str
    fields: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class RepairContext:
    issue_codes: tuple[str, ...]
    quality_score: int | None = None
    quality_breakdown: Mapping[str, int] | None = None


@dataclass(frozen=True, slots=True)
class PlatformAdaptationContext:
    job: ContentJob
    master_content: MasterContent
    sources: tuple[SourceEvidence, ...]
    revision: int
    repair: RepairContext | None = None


@dataclass(frozen=True, slots=True)
class PlatformValidationContext:
    job: ContentJob
    master_content: MasterContent
    sources: tuple[SourceEvidence, ...]


class Platform(Protocol):
    """One module-owned schema, prompt, parser, validator, and scorer."""

    @property
    def key(self) -> str: ...

    @property
    def display_name(self) -> str: ...

    @property
    def adaptation_guidance(self) -> str: ...

    @property
    def output_schema(self) -> AIOutputSchema: ...

    @property
    def schema_version(self) -> str: ...

    @property
    def supported_formats(self) -> tuple[str, ...]: ...

    def build_request(self, context: PlatformAdaptationContext) -> AIRequest: ...

    def parse_payload(self, payload: Mapping[str, Any]) -> PlatformPayload: ...

    def validate(
        self,
        content: PlatformContent | PlatformContentRecord,
        context: PlatformValidationContext | None = None,
    ) -> ValidationResult: ...

    def score(
        self,
        content: PlatformContentRecord,
        context: PlatformValidationContext,
        validation: ValidationResult,
        policy: QualityPolicy,
    ) -> QualityBreakdown: ...


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


def adaptation_context_mapping(
    context: PlatformAdaptationContext,
) -> Mapping[str, Any]:
    """Share only the persisted master artifact and its approved references with AI."""

    allowed_ids = set(context.master_content.source_ids)
    source_context = [
        {
            "id": source.id,
            "title": source.title,
            "url": source.url,
            "relevant_excerpt": source.relevant_excerpt,
        }
        for source in context.sources
        if source.id in allowed_ids
    ]
    result: dict[str, Any] = {
        "master_content": {
            "id": context.master_content.id,
            "title": context.master_content.title,
            "summary": context.master_content.summary,
            "body": context.master_content.body,
            "key_points": context.master_content.key_points,
            "source_ids": context.master_content.source_ids,
        },
        "approved_sources": source_context,
        "revision": context.revision,
    }
    if context.repair is not None:
        result["repair"] = {
            "issue_codes": context.repair.issue_codes,
            "quality_score": context.repair.quality_score,
            "quality_breakdown": dict(context.repair.quality_breakdown or {}),
        }
    return result


def validate_record_integrity(
    content: PlatformContentRecord,
    context: PlatformValidationContext,
    *,
    expected_platform: str,
    expected_schema_version: str,
    supported_formats: tuple[str, ...],
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if content.job_id != context.job.id:
        issues.append(
            ValidationIssue(
                code="job_linkage_mismatch",
                field="job_id",
                message="platform content belongs to a different content job",
            )
        )
    if content.master_content_id != context.master_content.id:
        issues.append(
            ValidationIssue(
                code="master_content_linkage_mismatch",
                field="master_content_id",
                message="platform content does not reference the persisted master content",
            )
        )
    if content.platform != expected_platform:
        issues.append(
            ValidationIssue(
                code="platform_mismatch",
                field="platform",
                message=f"platform must be {expected_platform}",
            )
        )
    if content.schema_version != expected_schema_version:
        issues.append(
            ValidationIssue(
                code="schema_version_mismatch",
                field="schema_version",
                message=f"schema version must be {expected_schema_version}",
            )
        )
    if content.format not in supported_formats:
        issues.append(
            ValidationIssue(
                code="unsupported_format",
                field="format",
                message=f"unsupported {expected_platform} format: {content.format}",
            )
        )
    if content.payload.format != content.format:
        issues.append(
            ValidationIssue(
                code="payload_format_mismatch",
                field="payload",
                message="payload format does not match the durable record",
            )
        )
    if content.payload.schema_version != content.schema_version:
        issues.append(
            ValidationIssue(
                code="payload_schema_mismatch",
                field="payload",
                message="payload schema does not match the durable record",
            )
        )

    source_by_id = {source.id: source for source in context.sources}
    allowed_ids = set(context.master_content.source_ids)
    for source_id in content.payload.source_ids:
        source = source_by_id.get(source_id)
        if source is None:
            issues.append(
                ValidationIssue(
                    code="unknown_source_reference",
                    field="source_references",
                    message=f"platform content references unknown source {source_id}",
                )
            )
            continue
        if source.job_id != content.job_id:
            issues.append(
                ValidationIssue(
                    code="cross_job_source_reference",
                    field="source_references",
                    message=f"source {source_id} belongs to a different content job",
                )
            )
        if source.evidence_status is not EvidenceStatus.REVIEWED:
            issues.append(
                ValidationIssue(
                    code="unreviewed_source_reference",
                    field="source_references",
                    message=f"source {source_id} has not been reviewed for use",
                )
            )
        if source_id not in allowed_ids:
            issues.append(
                ValidationIssue(
                    code="source_not_in_master_content",
                    field="source_references",
                    message=f"source {source_id} is not approved by the master content",
                )
            )
    return issues


def apply_validation_caps(
    breakdown: QualityBreakdown, validation: ValidationResult
) -> QualityBreakdown:
    """Make scoring consequences explicit without pretending to predict engagement."""

    if validation.is_valid:
        return breakdown
    evidence_codes = {
        "unknown_source_reference",
        "cross_job_source_reference",
        "unreviewed_source_reference",
        "source_not_in_master_content",
    }
    evidence_integrity = (
        0
        if any(issue.code in evidence_codes for issue in validation.issues)
        else breakdown.evidence_integrity
    )
    return QualityBreakdown(
        structure=0,
        completeness=min(breakdown.completeness, 10),
        platform_fit=min(breakdown.platform_fit, 10),
        evidence_integrity=evidence_integrity,
        content_hygiene=min(breakdown.content_hygiene, 10),
    )
