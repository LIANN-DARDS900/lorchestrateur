"""Instagram V1 creative-plan schemas, validation, and deterministic scoring."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar

from lorchestrateur.ai.contracts import AIOutputSchema, AIRequest, AITask
from lorchestrateur.ai.structured import StructuredOutputError
from lorchestrateur.domain.platform_content import (
    PlatformContentRecord,
    PlatformPayload,
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
    require_order,
    require_sequence,
    require_text,
)

SCHEMA_VERSION = "instagram_content_v1"
MIN_CAROUSEL_SLIDES = 2
MAX_CAROUSEL_SLIDES = 10
MAX_REEL_BEATS = 12
MAX_CAPTION_CHARACTERS = 2_200


class InstagramFormat(StrEnum):
    CAROUSEL = "carousel"
    REEL_CONCEPT = "reel_concept"
    IMAGE_POST_CONCEPT = "image_post_concept"


@dataclass(frozen=True, slots=True)
class InstagramSlideV1:
    order: int
    heading: str
    body: str

    def to_mapping(self) -> Mapping[str, Any]:
        return {"order": self.order, "heading": self.heading, "body": self.body}


@dataclass(frozen=True, slots=True)
class InstagramBeatV1:
    order: int
    scene: str
    message: str

    def to_mapping(self) -> Mapping[str, Any]:
        return {"order": self.order, "scene": self.scene, "message": self.message}


@dataclass(frozen=True, slots=True)
class InstagramCarouselV1:
    hook: str
    slides: tuple[InstagramSlideV1, ...]
    caption: str
    cta: str | None
    source_ids: tuple[str, ...]

    schema_version: ClassVar[str] = SCHEMA_VERSION
    format: ClassVar[str] = InstagramFormat.CAROUSEL.value

    def to_mapping(self) -> Mapping[str, Any]:
        return {
            "platform": "instagram",
            "schema_version": self.schema_version,
            "format": self.format,
            "hook": self.hook,
            "slides": [slide.to_mapping() for slide in self.slides],
            "caption": self.caption,
            "cta": self.cta,
            "source_references": list(self.source_ids),
        }


@dataclass(frozen=True, slots=True)
class InstagramReelV1:
    opening_hook: str
    beats: tuple[InstagramBeatV1, ...]
    caption: str
    cta: str | None
    source_ids: tuple[str, ...]

    schema_version: ClassVar[str] = SCHEMA_VERSION
    format: ClassVar[str] = InstagramFormat.REEL_CONCEPT.value

    def to_mapping(self) -> Mapping[str, Any]:
        return {
            "platform": "instagram",
            "schema_version": self.schema_version,
            "format": self.format,
            "opening_hook": self.opening_hook,
            "beats": [beat.to_mapping() for beat in self.beats],
            "caption": self.caption,
            "cta": self.cta,
            "source_references": list(self.source_ids),
        }


@dataclass(frozen=True, slots=True)
class InstagramImagePostV1:
    hook: str
    visual_concept: str
    caption: str
    cta: str | None
    source_ids: tuple[str, ...]

    schema_version: ClassVar[str] = SCHEMA_VERSION
    format: ClassVar[str] = InstagramFormat.IMAGE_POST_CONCEPT.value

    def to_mapping(self) -> Mapping[str, Any]:
        return {
            "platform": "instagram",
            "schema_version": self.schema_version,
            "format": self.format,
            "hook": self.hook,
            "visual_concept": self.visual_concept,
            "caption": self.caption,
            "cta": self.cta,
            "source_references": list(self.source_ids),
        }


InstagramContentV1 = InstagramCarouselV1 | InstagramReelV1 | InstagramImagePostV1


def _base_contract(payload: Mapping[str, Any], required_fields: frozenset[str]) -> None:
    require_contract(
        payload,
        platform="instagram",
        schema_version=SCHEMA_VERSION,
        required_fields=required_fields,
        optional_fields=frozenset({"cta"}),
    )


def _parse_carousel(payload: Mapping[str, Any]) -> InstagramCarouselV1:
    _base_contract(
        payload,
        frozenset({"format", "hook", "slides", "caption", "source_references"}),
    )
    slides: list[InstagramSlideV1] = []
    for item in require_sequence(payload, "slides"):
        if not isinstance(item, Mapping) or set(item) != {"order", "heading", "body"}:
            raise StructuredOutputError(
                "invalid_slide", "each carousel slide requires order, heading, and body"
            )
        slides.append(
            InstagramSlideV1(
                order=require_order(item.get("order")),
                heading=require_text(item, "heading"),
                body=require_text(item, "body"),
            )
        )
    return InstagramCarouselV1(
        hook=require_text(payload, "hook"),
        slides=tuple(slides),
        caption=require_text(payload, "caption"),
        cta=optional_text(payload, "cta"),
        source_ids=parse_source_references(payload),
    )


def _parse_reel(payload: Mapping[str, Any]) -> InstagramReelV1:
    _base_contract(
        payload,
        frozenset({"format", "opening_hook", "beats", "caption", "source_references"}),
    )
    beats: list[InstagramBeatV1] = []
    for item in require_sequence(payload, "beats"):
        if not isinstance(item, Mapping) or set(item) != {"order", "scene", "message"}:
            raise StructuredOutputError(
                "invalid_reel_beat", "each reel beat requires order, scene, and message"
            )
        beats.append(
            InstagramBeatV1(
                order=require_order(item.get("order")),
                scene=require_text(item, "scene"),
                message=require_text(item, "message"),
            )
        )
    return InstagramReelV1(
        opening_hook=require_text(payload, "opening_hook"),
        beats=tuple(beats),
        caption=require_text(payload, "caption"),
        cta=optional_text(payload, "cta"),
        source_ids=parse_source_references(payload),
    )


def _parse_image_post(payload: Mapping[str, Any]) -> InstagramImagePostV1:
    _base_contract(
        payload,
        frozenset({"format", "hook", "visual_concept", "caption", "source_references"}),
    )
    return InstagramImagePostV1(
        hook=require_text(payload, "hook"),
        visual_concept=require_text(payload, "visual_concept"),
        caption=require_text(payload, "caption"),
        cta=optional_text(payload, "cta"),
        source_ids=parse_source_references(payload),
    )


class InstagramPlatform:
    key = "instagram"
    display_name = "Instagram"
    adaptation_guidance = (
        "Create a structured carousel, reel concept, or image-post concept from MasterContent. "
        "Describe the creative plan only; do not generate media or introduce new factual claims."
    )
    output_schema = AIOutputSchema.INSTAGRAM_CONTENT_V1
    schema_version = SCHEMA_VERSION
    supported_formats = tuple(item.value for item in InstagramFormat)

    def build_request(self, context: PlatformAdaptationContext) -> AIRequest:
        task = (
            AITask.CONTROLLED_REWRITE if context.repair is not None else AITask.PLATFORM_ADAPTATION
        )
        return AIRequest(
            task=task,
            prompt=(
                f"{self.adaptation_guidance} Return exactly instagram_content_v1. A carousel "
                "uses hook, ordered slides, caption, optional cta, and source_references; a reel "
                "concept uses opening_hook, ordered beats, caption, optional cta, and references; "
                "an image-post concept uses hook, visual_concept, caption, optional cta, and "
                "references."
            ),
            context=adaptation_context_mapping(context),
            max_output_characters=18_000,
            output_schema=self.output_schema,
        )

    def parse_payload(self, payload: Mapping[str, Any]) -> PlatformPayload:
        raw_format = payload.get("format")
        if raw_format == InstagramFormat.CAROUSEL:
            return _parse_carousel(payload)
        if raw_format == InstagramFormat.REEL_CONCEPT:
            return _parse_reel(payload)
        if raw_format == InstagramFormat.IMAGE_POST_CONCEPT:
            return _parse_image_post(payload)
        raise StructuredOutputError(
            "unsupported_format",
            "Instagram format must be carousel, reel_concept, or image_post_concept",
        )

    def validate(
        self,
        content: PlatformContent | PlatformContentRecord,
        context: PlatformValidationContext | None = None,
    ) -> ValidationResult:
        if isinstance(content, PlatformContent):
            issues = []
            if content.platform != self.key:
                issues.append(ValidationIssue("platform_mismatch", "wrong platform"))
            if not content.fields.get("caption", "").strip():
                issues.append(ValidationIssue("required", "caption is required", "caption"))
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
        payload = content.payload
        if not isinstance(payload, (InstagramCarouselV1, InstagramReelV1, InstagramImagePostV1)):
            issues.append(
                ValidationIssue("payload_type_mismatch", "invalid Instagram V1 payload", "payload")
            )
            return ValidationResult(tuple(issues))
        if len(payload.caption) > MAX_CAPTION_CHARACTERS:
            issues.append(
                ValidationIssue(
                    "caption_too_long",
                    f"caption exceeds {MAX_CAPTION_CHARACTERS} characters",
                    "caption",
                )
            )
        if isinstance(payload, InstagramCarouselV1):
            if not MIN_CAROUSEL_SLIDES <= len(payload.slides) <= MAX_CAROUSEL_SLIDES:
                issues.append(
                    ValidationIssue(
                        "carousel_slide_count",
                        f"carousel requires {MIN_CAROUSEL_SLIDES} to {MAX_CAROUSEL_SLIDES} slides",
                        "slides",
                    )
                )
            orders = tuple(slide.order for slide in payload.slides)
            if orders != tuple(range(1, len(payload.slides) + 1)):
                issues.append(
                    ValidationIssue(
                        "invalid_slide_order",
                        "slide order must start at 1 and be contiguous",
                        "slides",
                    )
                )
        if isinstance(payload, InstagramReelV1):
            if len(payload.beats) > MAX_REEL_BEATS:
                issues.append(
                    ValidationIssue(
                        "reel_beat_count",
                        f"reel concept cannot exceed {MAX_REEL_BEATS} beats",
                        "beats",
                    )
                )
            orders = tuple(beat.order for beat in payload.beats)
            if orders != tuple(range(1, len(payload.beats) + 1)):
                issues.append(
                    ValidationIssue(
                        "invalid_beat_order",
                        "beat order must start at 1 and be contiguous",
                        "beats",
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
        if not isinstance(payload, (InstagramCarouselV1, InstagramReelV1, InstagramImagePostV1)):
            return QualityBreakdown(0, 0, 0, 0, 0)
        if isinstance(payload, InstagramCarouselV1):
            fit = 20 if 3 <= len(payload.slides) <= MAX_CAROUSEL_SLIDES else 15
        elif isinstance(payload, InstagramReelV1):
            fit = 20 if 2 <= len(payload.beats) <= 8 else 15
        else:
            fit = 20
        breakdown = QualityBreakdown(
            structure=20,
            completeness=20 if payload.cta else 15,
            platform_fit=fit,
            evidence_integrity=(
                20 if set(payload.source_ids) == set(context.master_content.source_ids) else 15
            ),
            content_hygiene=20,
        )
        return apply_validation_caps(breakdown, validation)


INSTAGRAM = InstagramPlatform()
