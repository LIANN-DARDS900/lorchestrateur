"""Blog V1 adaptation contract, typed payload, validation, and scoring."""

from __future__ import annotations

import re
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
    parse_string_sequence,
    require_contract,
    require_sequence,
    require_text,
)

SCHEMA_VERSION = "blog_content_v1"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class BlogSectionV1:
    heading: str
    body: str

    def to_mapping(self) -> Mapping[str, str]:
        return {"heading": self.heading, "body": self.body}


@dataclass(frozen=True, slots=True)
class BlogContentV1:
    title: str
    slug_suggestion: str
    excerpt: str
    introduction: str
    sections: tuple[BlogSectionV1, ...]
    conclusion: str
    cta: str | None
    seo_title: str
    meta_description: str
    source_ids: tuple[str, ...]
    internal_link_suggestions: tuple[str, ...] = ()

    schema_version: ClassVar[str] = SCHEMA_VERSION
    format: ClassVar[str] = "article"

    def to_mapping(self) -> Mapping[str, Any]:
        return {
            "platform": "blog",
            "schema_version": self.schema_version,
            "format": self.format,
            "title": self.title,
            "slug_suggestion": self.slug_suggestion,
            "excerpt": self.excerpt,
            "introduction": self.introduction,
            "sections": [section.to_mapping() for section in self.sections],
            "conclusion": self.conclusion,
            "cta": self.cta,
            "seo_title": self.seo_title,
            "meta_description": self.meta_description,
            "source_references": list(self.source_ids),
            "internal_link_suggestions": list(self.internal_link_suggestions),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> BlogContentV1:
        required = frozenset(
            {
                "format",
                "title",
                "slug_suggestion",
                "excerpt",
                "introduction",
                "sections",
                "conclusion",
                "seo_title",
                "meta_description",
                "source_references",
            }
        )
        require_contract(
            payload,
            platform="blog",
            schema_version=SCHEMA_VERSION,
            required_fields=required,
            optional_fields=frozenset({"cta", "internal_link_suggestions"}),
        )
        if payload.get("format") != cls.format:
            raise StructuredOutputError("unsupported_format", "blog format must be 'article'")
        sections: list[BlogSectionV1] = []
        for item in require_sequence(payload, "sections"):
            if not isinstance(item, Mapping):
                raise StructuredOutputError(
                    "invalid_section", "each blog section must be an object"
                )
            if set(item) != {"heading", "body"}:
                raise StructuredOutputError(
                    "invalid_section", "blog sections require only heading and body"
                )
            sections.append(
                BlogSectionV1(
                    heading=require_text(item, "heading"),
                    body=require_text(item, "body"),
                )
            )
        return cls(
            title=require_text(payload, "title"),
            slug_suggestion=require_text(payload, "slug_suggestion"),
            excerpt=require_text(payload, "excerpt"),
            introduction=require_text(payload, "introduction"),
            sections=tuple(sections),
            conclusion=require_text(payload, "conclusion"),
            cta=optional_text(payload, "cta"),
            seo_title=require_text(payload, "seo_title"),
            meta_description=require_text(payload, "meta_description"),
            source_ids=parse_source_references(payload),
            internal_link_suggestions=parse_string_sequence(
                payload.get("internal_link_suggestions"),
                "internal_link_suggestions",
                required=False,
            ),
        )


class BlogPlatform:
    key = "blog"
    display_name = "Blog"
    adaptation_guidance = (
        "Build an evidence-aware article from the canonical argument. Use natural SEO metadata, "
        "clear sections, and source IDs supplied by MasterContent; never add unsupported claims."
    )
    output_schema = AIOutputSchema.BLOG_CONTENT_V1
    schema_version = SCHEMA_VERSION
    supported_formats = ("article",)

    def build_request(self, context: PlatformAdaptationContext) -> AIRequest:
        task = (
            AITask.CONTROLLED_REWRITE if context.repair is not None else AITask.PLATFORM_ADAPTATION
        )
        return AIRequest(
            task=task,
            prompt=(
                f"{self.adaptation_guidance} Return exactly the blog_content_v1 contract with "
                "platform, schema_version, format, title, slug_suggestion, excerpt, introduction, "
                "sections, conclusion, optional cta, seo_title, meta_description, "
                "source_references, and optional internal_link_suggestions."
            ),
            context=adaptation_context_mapping(context),
            max_output_characters=40_000,
            output_schema=self.output_schema,
        )

    def parse_payload(self, payload: Mapping[str, Any]) -> BlogContentV1:
        return BlogContentV1.from_mapping(payload)

    def validate(
        self,
        content: PlatformContent | PlatformContentRecord,
        context: PlatformValidationContext | None = None,
    ) -> ValidationResult:
        if isinstance(content, PlatformContent):
            issues = []
            if content.platform != self.key:
                issues.append(ValidationIssue("platform_mismatch", "wrong platform"))
            if not content.fields.get("title", "").strip():
                issues.append(ValidationIssue("required", "title is required", "title"))
            if not content.fields.get("body", "").strip():
                issues.append(ValidationIssue("required", "body is required", "body"))
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
        if not isinstance(content.payload, BlogContentV1):
            issues.append(
                ValidationIssue("payload_type_mismatch", "payload must be BlogContentV1", "payload")
            )
            return ValidationResult(tuple(issues))
        payload = content.payload
        if len(payload.title) > 120:
            issues.append(
                ValidationIssue("title_too_long", "title exceeds 120 characters", "title")
            )
        if len(payload.slug_suggestion) > 100 or not SLUG_PATTERN.fullmatch(
            payload.slug_suggestion
        ):
            issues.append(
                ValidationIssue(
                    "invalid_slug",
                    "slug must be lowercase hyphen-separated text",
                    "slug_suggestion",
                )
            )
        if len(payload.excerpt) > 300:
            issues.append(
                ValidationIssue("excerpt_too_long", "excerpt exceeds 300 characters", "excerpt")
            )
        if len(payload.seo_title) > 60:
            issues.append(
                ValidationIssue(
                    "seo_title_too_long", "SEO title exceeds 60 characters", "seo_title"
                )
            )
        if len(payload.meta_description) > 160:
            issues.append(
                ValidationIssue(
                    "meta_description_too_long",
                    "meta description exceeds 160 characters",
                    "meta_description",
                )
            )
        headings = tuple(section.heading.casefold() for section in payload.sections)
        if len(headings) != len(set(headings)):
            issues.append(
                ValidationIssue(
                    "duplicate_sections", "blog section headings must be unique", "sections"
                )
            )
        if payload.introduction.casefold() == payload.conclusion.casefold():
            issues.append(
                ValidationIssue(
                    "duplicate_intro_conclusion",
                    "introduction and conclusion must be distinct",
                    "conclusion",
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
        if not isinstance(payload, BlogContentV1):
            return QualityBreakdown(0, 0, 0, 0, 0)
        referenced = set(payload.source_ids)
        master_sources = set(context.master_content.source_ids)
        breakdown = QualityBreakdown(
            structure=20,
            completeness=20 if payload.cta else 15,
            platform_fit=20 if len(payload.sections) >= 2 else 15,
            evidence_integrity=20 if referenced == master_sources else 15,
            content_hygiene=20 if payload.title.casefold() != payload.seo_title.casefold() else 15,
        )
        return apply_validation_caps(breakdown, validation)


BLOG = BlogPlatform()
