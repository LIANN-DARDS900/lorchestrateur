"""X V1 single-post/thread contract, validation, and deterministic scoring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
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
    ContentFieldRule,
    PlatformAdaptationContext,
    PlatformContent,
    PlatformValidationContext,
    SchemaPlatform,
    adaptation_context_mapping,
    apply_validation_caps,
    validate_record_integrity,
)
from lorchestrateur.platforms.parsing import (
    optional_text,
    parse_source_references,
    require_contract,
    require_order,
    require_sequence,
    require_text,
)

SCHEMA_VERSION = "x_content_v1"
STANDARD_POST_MAX_CHARACTERS = 280
MAX_THREAD_POSTS = 25


class XFormat(StrEnum):
    SINGLE_POST = "single_post"
    THREAD = "thread"


@dataclass(frozen=True, slots=True)
class XPostV1:
    order: int
    text: str

    def to_mapping(self) -> Mapping[str, Any]:
        return {"order": self.order, "text": self.text}


@dataclass(frozen=True, slots=True)
class XContentV1:
    format: str
    opening_hook: str
    posts: tuple[XPostV1, ...]
    cta: str | None
    source_ids: tuple[str, ...]

    schema_version: ClassVar[str] = SCHEMA_VERSION

    def to_mapping(self) -> Mapping[str, Any]:
        return {
            "platform": "x",
            "schema_version": self.schema_version,
            "format": self.format,
            "opening_hook": self.opening_hook,
            "posts": [post.to_mapping() for post in self.posts],
            "cta": self.cta,
            "source_references": list(self.source_ids),
        }

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> XContentV1:
        require_contract(
            payload,
            platform="x",
            schema_version=SCHEMA_VERSION,
            required_fields=frozenset({"format", "opening_hook", "posts", "source_references"}),
            optional_fields=frozenset({"cta"}),
        )
        raw_format = require_text(payload, "format")
        try:
            format_value = XFormat(raw_format).value
        except ValueError as exc:
            raise StructuredOutputError(
                "unsupported_format", "X format must be single_post or thread"
            ) from exc
        posts: list[XPostV1] = []
        for item in require_sequence(payload, "posts"):
            if not isinstance(item, Mapping) or set(item) != {"order", "text"}:
                raise StructuredOutputError(
                    "invalid_post", "each X post requires only order and text"
                )
            posts.append(
                XPostV1(
                    order=require_order(item.get("order")),
                    text=require_text(item, "text"),
                )
            )
        return cls(
            format=format_value,
            opening_hook=require_text(payload, "opening_hook"),
            posts=tuple(posts),
            cta=optional_text(payload, "cta"),
            source_ids=parse_source_references(payload),
        )


class XPlatform:
    key = "x"
    display_name = "X"
    adaptation_guidance = (
        "Create either one standard X post or a coherent standard-post thread. Keep every post "
        "within 280 characters, preserve evidence, and do not assume paid long-post entitlement."
    )
    output_schema = AIOutputSchema.X_CONTENT_V1
    schema_version = SCHEMA_VERSION
    supported_formats = tuple(item.value for item in XFormat)
    _legacy_schema = SchemaPlatform(
        key="x",
        display_name="X",
        adaptation_guidance=adaptation_guidance,
        field_rules={"text": ContentFieldRule(max_length=STANDARD_POST_MAX_CHARACTERS)},
    )

    def build_request(self, context: PlatformAdaptationContext) -> AIRequest:
        task = (
            AITask.CONTROLLED_REWRITE if context.repair is not None else AITask.PLATFORM_ADAPTATION
        )
        return AIRequest(
            task=task,
            prompt=(
                f"{self.adaptation_guidance} Return exactly x_content_v1 with platform, "
                "schema_version, format, opening_hook, ordered posts, optional cta, and "
                "source_references."
            ),
            context=adaptation_context_mapping(context),
            max_output_characters=12_000,
            output_schema=self.output_schema,
        )

    def parse_payload(self, payload: Mapping[str, Any]) -> XContentV1:
        return XContentV1.from_mapping(payload)

    def validate(
        self,
        content: PlatformContent | PlatformContentRecord,
        context: PlatformValidationContext | None = None,
    ) -> ValidationResult:
        if isinstance(content, PlatformContent):
            return self._legacy_schema.validate(content)
        if context is None:
            raise ValueError("platform validation context is required")
        issues = validate_record_integrity(
            content,
            context,
            expected_platform=self.key,
            expected_schema_version=self.schema_version,
            supported_formats=self.supported_formats,
        )
        if not isinstance(content.payload, XContentV1):
            issues.append(
                ValidationIssue("payload_type_mismatch", "payload must be XContentV1", "payload")
            )
            return ValidationResult(tuple(issues))
        payload = content.payload
        expected_orders = tuple(range(1, len(payload.posts) + 1))
        actual_orders = tuple(post.order for post in payload.posts)
        if actual_orders != expected_orders:
            issues.append(
                ValidationIssue(
                    "invalid_thread_order",
                    "X post order must be contiguous and start at 1",
                    "posts",
                )
            )
        if payload.format == XFormat.SINGLE_POST and len(payload.posts) != 1:
            issues.append(
                ValidationIssue(
                    "single_post_count",
                    "single_post format requires exactly one post",
                    "posts",
                )
            )
        if payload.format == XFormat.THREAD and not 2 <= len(payload.posts) <= MAX_THREAD_POSTS:
            issues.append(
                ValidationIssue(
                    "thread_post_count",
                    f"thread format requires between 2 and {MAX_THREAD_POSTS} posts",
                    "posts",
                )
            )
        for post in payload.posts:
            if len(post.text) > STANDARD_POST_MAX_CHARACTERS:
                issues.append(
                    ValidationIssue(
                        "standard_post_too_long",
                        f"post {post.order} exceeds {STANDARD_POST_MAX_CHARACTERS} characters",
                        f"posts[{post.order}]",
                    )
                )
        texts = tuple(post.text.casefold() for post in payload.posts)
        if len(texts) != len(set(texts)):
            issues.append(ValidationIssue("duplicate_posts", "X posts must be distinct", "posts"))
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
        if not isinstance(payload, XContentV1):
            return QualityBreakdown(0, 0, 0, 0, 0)
        texts = tuple(post.text for post in payload.posts)
        breakdown = QualityBreakdown(
            structure=20,
            completeness=20 if payload.cta else 15,
            platform_fit=20
            if all(len(text) <= STANDARD_POST_MAX_CHARACTERS for text in texts)
            else 0,
            evidence_integrity=(
                20 if set(payload.source_ids) == set(context.master_content.source_ids) else 15
            ),
            content_hygiene=20 if len(texts) == len(set(text.casefold() for text in texts)) else 0,
        )
        return apply_validation_caps(breakdown, validation)


X = XPlatform()
