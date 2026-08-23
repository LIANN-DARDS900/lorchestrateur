"""Deterministic validation for evidence and generated content artifacts."""

from __future__ import annotations

import json
from collections.abc import Iterable
from urllib.parse import urlparse

from lorchestrateur.domain.content import (
    ContentStrategy,
    EvidenceStatus,
    MasterContent,
    SourceEvidence,
)
from lorchestrateur.domain.validation import ValidationIssue, ValidationResult


class ContentValidationError(ValueError):
    def __init__(self, result: ValidationResult) -> None:
        self.result = result
        codes = ", ".join(issue.code for issue in result.issues)
        super().__init__(f"content validation failed: {codes}")


def validate_source(source: SourceEvidence) -> ValidationResult:
    issues: list[ValidationIssue] = []
    if source.url is not None:
        parsed = urlparse(source.url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            issues.append(
                ValidationIssue(
                    code="invalid_source_url",
                    field="url",
                    message="source URL must be an absolute HTTP or HTTPS URL",
                )
            )
    try:
        json.dumps(dict(source.metadata))
    except (TypeError, ValueError):
        issues.append(
            ValidationIssue(
                code="source_metadata_not_json_serializable",
                field="metadata",
                message="source metadata must contain JSON-compatible values",
            )
        )
    return ValidationResult(tuple(issues))


def validate_research_sources(sources: Iterable[SourceEvidence]) -> ValidationResult:
    source_list = tuple(sources)
    issues: list[ValidationIssue] = []
    ids = tuple(source.id for source in source_list)
    if not source_list:
        issues.append(
            ValidationIssue(
                code="sources_required",
                message="at least one source is required before strategy generation",
            )
        )
    if len(ids) != len(set(ids)):
        issues.append(
            ValidationIssue(
                code="duplicate_source_id",
                message="research sources contain duplicate IDs",
            )
        )
    if source_list and not any(
        source.evidence_status is EvidenceStatus.REVIEWED for source in source_list
    ):
        issues.append(
            ValidationIssue(
                code="reviewed_source_required",
                message="at least one reviewed source is required",
            )
        )
    return ValidationResult(tuple(issues))


def validate_strategy(
    strategy: ContentStrategy,
    sources: Iterable[SourceEvidence],
) -> ValidationResult:
    source_by_id = {source.id: source for source in sources}
    issues: list[ValidationIssue] = []

    for source_id in strategy.supporting_source_ids:
        source = source_by_id.get(source_id)
        if source is None:
            issues.append(
                ValidationIssue(
                    code="unknown_source_reference",
                    field="key_messages",
                    message=f"strategy references unknown source {source_id}",
                )
            )
            continue
        if source.job_id != strategy.job_id:
            issues.append(
                ValidationIssue(
                    code="cross_job_source_reference",
                    field="key_messages",
                    message=f"source {source_id} belongs to a different content job",
                )
            )
        if source.evidence_status is not EvidenceStatus.REVIEWED:
            issues.append(
                ValidationIssue(
                    code="unreviewed_source_reference",
                    field="key_messages",
                    message=f"source {source_id} has not been reviewed for use",
                )
            )

    return ValidationResult(tuple(issues))


def validate_master_content(
    master_content: MasterContent,
    strategy: ContentStrategy,
    sources: Iterable[SourceEvidence],
) -> ValidationResult:
    source_by_id = {source.id: source for source in sources}
    strategy_source_ids = set(strategy.supporting_source_ids)
    issues: list[ValidationIssue] = []

    if master_content.job_id != strategy.job_id:
        issues.append(
            ValidationIssue(
                code="strategy_job_mismatch",
                message="master content and strategy belong to different jobs",
            )
        )

    for source_id in master_content.source_ids:
        source = source_by_id.get(source_id)
        if source is None:
            issues.append(
                ValidationIssue(
                    code="unknown_source_reference",
                    field="source_ids",
                    message=f"master content references unknown source {source_id}",
                )
            )
            continue
        if source.job_id != master_content.job_id:
            issues.append(
                ValidationIssue(
                    code="cross_job_source_reference",
                    field="source_ids",
                    message=f"source {source_id} belongs to a different content job",
                )
            )
        if source.evidence_status is not EvidenceStatus.REVIEWED:
            issues.append(
                ValidationIssue(
                    code="unreviewed_source_reference",
                    field="source_ids",
                    message=f"source {source_id} has not been reviewed for use",
                )
            )
        if source_id not in strategy_source_ids:
            issues.append(
                ValidationIssue(
                    code="source_not_in_strategy",
                    field="source_ids",
                    message=f"source {source_id} was not selected by the content strategy",
                )
            )

    if master_content.summary == master_content.body:
        issues.append(
            ValidationIssue(
                code="summary_duplicates_body",
                field="summary",
                message="summary must be distinct from the canonical body",
            )
        )

    return ValidationResult(tuple(issues))
