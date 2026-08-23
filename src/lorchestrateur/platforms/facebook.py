"""Facebook V1 adaptation contract, validation, and deterministic scoring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from lorchestrateur.ai.contracts import AIOutputSchema, AIRequest, AITask
from lorchestrateur.ai.structured import StructuredOutputError
from lorchestrateur.domain.platform_content import (
    PlatformContentRecord,
    QualityBreakdown,
    QualityPolicy,
)
from lorchestrateur.domain.validation import ValidationIssue, ValidationResult
from lorchestrateur.platforms.contracts import (
    PlatformAdaptationContext,
    PlatformContent,
    PlatformValidationContext,
    adaptation_context_mapping,
    apply_validation_caps,
    validate_record_integrity,
)
from lorchestrateur.platforms.parsing import (
    optional_text,
    parse_source_references,
    require_contract,
    require_text,
)

SCHEMA_VERSION = "facebook_content_v1"


@dataclass(frozen=True, slots=True)
class FacebookContentV1:
    opening: str
    body: str
    cta: str | None
    link_context_recommendation: str | None
    source_ids: tuple[str, ...]

    schema_version: ClassVar[str] = SCHEMA_VERSION
    format: ClassVar[str] = "story_post"

    def to_mapping(self) -> Mapping[str, Any]:
        return {
            "platform": "facebook",
            "schema_version": self.schema_version,
            "format": self.format,
            "opening": self.opening,
            "body": self.body,
            "cta": self.cta,
            "link_context_recommendation": self.link_context_recommendation,
            "source_references": list(self.source_ids),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> FacebookContentV1:
        require_contract(
            payload,
            platform="facebook",
            schema_version=SCHEMA_VERSION,
            required_fields=frozenset({"format", "opening", "body", "source_references"}),
            optional_fields=frozenset({"cta", "link_context_recommendation"}),
        )
        if payload.get("format") != cls.format:
            raise StructuredOutputError("unsupported_format", "Facebook format must be story_post")
        return cls(
            opening=require_text(payload, "opening"),
            body=require_text(payload, "body"),
            cta=optional_text(payload, "cta"),
            link_context_recommendation=optional_text(payload, "link_context_recommendation"),
            source_ids=parse_source_references(payload),
        )


class FacebookPlatform:
    key = "facebook"
    display_name = "Facebook"
    adaptation_guidance = (
        "Create a contextual, story-oriented Facebook post from MasterContent. Keep its opening "
        "and body distinct from X or Instagram conventions and preserve approved evidence."
    )
    output_schema = AIOutputSchema.FACEBOOK_CONTENT_V1
    schema_version = SCHEMA_VERSION
    supported_formats = ("story_post",)

    def build_request(self, context: PlatformAdaptationContext) -> AIRequest:
        task = (
            AITask.CONTROLLED_REWRITE if context.repair is not None else AITask.PLATFORM_ADAPTATION
        )
        return AIRequest(
            task=task,
            prompt=(
                f"{self.adaptation_guidance} Return exactly facebook_content_v1 with platform, "
                "schema_version, story_post format, opening, body, optional cta, optional "
                "link_context_recommendation, and source_references."
            ),
            context=adaptation_context_mapping(context),
            max_output_characters=14_000,
            output_schema=self.output_schema,
        )

    def parse_payload(self, payload: Mapping[str, Any]) -> FacebookContentV1:
        return FacebookContentV1.from_mapping(payload)

    def validate(
        self,
        content: PlatformContent | PlatformContentRecord,
        context: PlatformValidationContext | None = None,
    ) -> ValidationResult:
        if isinstance(content, PlatformContent):
            issues = []
            if content.platform != self.key:
                issues.append(ValidationIssue("platform_mismatch", "wrong platform"))
            if not content.fields.get("text", "").strip():
                issues.append(ValidationIssue("required", "text is required", "text"))
            return ValidationResult(tuple(issues))
        if context is None:
            raise ValueError("platform validation context is required")
        issues = validate_record_integrity(
            content,
            context,
            expected_platform=self.key,
            expected_schema_version=self.schema_version,
            supported_formats=self.supported_formats,
        )
        if not isinstance(content.payload, FacebookContentV1):
            issues.append(
                ValidationIssue(
                    "payload_type_mismatch", "payload must be FacebookContentV1", "payload"
                )
            )
            return ValidationResult(tuple(issues))
        if content.payload.opening.casefold() == content.payload.body.casefold():
            issues.append(
                ValidationIssue(
                    "opening_duplicates_body",
                    "Facebook opening and contextual body must be distinct",
                    "body",
                )
            )
        return ValidationResult(tuple(issues))

    def score(
        self,
        content: PlatformContentRecord,
        context: PlatformValidationContext,
        validation: ValidationResult,
        policy: QualityPolicy,
    ) -> QualityBreakdown:
        del policy
        payload = content.payload
        if not isinstance(payload, FacebookContentV1):
            return QualityBreakdown(0, 0, 0, 0, 0)
        breakdown = QualityBreakdown(
            structure=20,
            completeness=20 if payload.cta else 15,
            platform_fit=20 if len(payload.body) > len(payload.opening) else 15,
            evidence_integrity=(
                20 if set(payload.source_ids) == set(context.master_content.source_ids) else 15
            ),
            content_hygiene=20 if payload.opening.casefold() != payload.body.casefold() else 0,
        )
        return apply_validation_caps(breakdown, validation)


FACEBOOK = FacebookPlatform()
