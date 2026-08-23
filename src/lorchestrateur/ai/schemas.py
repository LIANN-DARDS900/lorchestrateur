"""Provider-facing JSON Schemas for the authoritative structured-output contracts."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from lorchestrateur.ai.contracts import AIOutputSchema


def _object(properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _text_array() -> dict[str, Any]:
    return {"type": "array", "items": {"type": "string"}, "minItems": 1}


CONTENT_STRATEGY_SCHEMA = _object(
    {
        "objective": {"type": "string"},
        "target_audience": {"type": "string"},
        "angle": {"type": "string"},
        "tone": {"type": "string"},
        "key_messages": {
            "type": "array",
            "minItems": 1,
            "items": _object(
                {
                    "message": {"type": "string"},
                    "source_ids": _text_array(),
                },
                ("message", "source_ids"),
            ),
        },
        "intended_outcome": {"type": "string"},
    },
    (
        "objective",
        "target_audience",
        "angle",
        "tone",
        "key_messages",
        "intended_outcome",
    ),
)

MASTER_CONTENT_SCHEMA = _object(
    {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "body": {"type": "string"},
        "key_points": _text_array(),
        "source_ids": _text_array(),
    },
    ("title", "summary", "body", "key_points", "source_ids"),
)

BLOG_CONTENT_SCHEMA = _object(
    {
        "platform": {"type": "string", "enum": ["blog"]},
        "schema_version": {"type": "string", "enum": ["blog_content_v1"]},
        "format": {"type": "string", "enum": ["article"]},
        "title": {"type": "string"},
        "slug_suggestion": {"type": "string"},
        "excerpt": {"type": "string"},
        "introduction": {"type": "string"},
        "sections": {
            "type": "array",
            "minItems": 1,
            "items": _object(
                {"heading": {"type": "string"}, "body": {"type": "string"}},
                ("heading", "body"),
            ),
        },
        "conclusion": {"type": "string"},
        "cta": {"type": "string"},
        "seo_title": {"type": "string"},
        "meta_description": {"type": "string"},
        "source_references": _text_array(),
        "internal_link_suggestions": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    (
        "platform",
        "schema_version",
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
    ),
)

X_CONTENT_SCHEMA = _object(
    {
        "platform": {"type": "string", "enum": ["x"]},
        "schema_version": {"type": "string", "enum": ["x_content_v1"]},
        "format": {"type": "string", "enum": ["single_post", "thread"]},
        "opening_hook": {"type": "string"},
        "posts": {
            "type": "array",
            "minItems": 1,
            "maxItems": 25,
            "items": _object(
                {"order": {"type": "integer"}, "text": {"type": "string"}},
                ("order", "text"),
            ),
        },
        "cta": {"type": "string"},
        "source_references": _text_array(),
    },
    (
        "platform",
        "schema_version",
        "format",
        "opening_hook",
        "posts",
        "source_references",
    ),
)

INSTAGRAM_CONTENT_SCHEMA = _object(
    {
        "platform": {"type": "string", "enum": ["instagram"]},
        "schema_version": {"type": "string", "enum": ["instagram_content_v1"]},
        "format": {
            "type": "string",
            "enum": ["carousel", "reel_concept", "image_post_concept"],
        },
        "hook": {"type": "string"},
        "opening_hook": {"type": "string"},
        "slides": {
            "type": "array",
            "items": _object(
                {
                    "order": {"type": "integer"},
                    "heading": {"type": "string"},
                    "body": {"type": "string"},
                },
                ("order", "heading", "body"),
            ),
        },
        "beats": {
            "type": "array",
            "items": _object(
                {
                    "order": {"type": "integer"},
                    "scene": {"type": "string"},
                    "message": {"type": "string"},
                },
                ("order", "scene", "message"),
            ),
        },
        "visual_concept": {"type": "string"},
        "caption": {"type": "string"},
        "cta": {"type": "string"},
        "source_references": _text_array(),
    },
    ("platform", "schema_version", "format", "caption", "source_references"),
)

FACEBOOK_CONTENT_SCHEMA = _object(
    {
        "platform": {"type": "string", "enum": ["facebook"]},
        "schema_version": {"type": "string", "enum": ["facebook_content_v1"]},
        "format": {"type": "string", "enum": ["story_post"]},
        "opening": {"type": "string"},
        "body": {"type": "string"},
        "cta": {"type": "string"},
        "link_context_recommendation": {"type": "string"},
        "source_references": _text_array(),
    },
    (
        "platform",
        "schema_version",
        "format",
        "opening",
        "body",
        "source_references",
    ),
)


_SCHEMAS = {
    AIOutputSchema.CONTENT_STRATEGY_V1: CONTENT_STRATEGY_SCHEMA,
    AIOutputSchema.MASTER_CONTENT_V1: MASTER_CONTENT_SCHEMA,
    AIOutputSchema.BLOG_CONTENT_V1: BLOG_CONTENT_SCHEMA,
    AIOutputSchema.X_CONTENT_V1: X_CONTENT_SCHEMA,
    AIOutputSchema.INSTAGRAM_CONTENT_V1: INSTAGRAM_CONTENT_SCHEMA,
    AIOutputSchema.FACEBOOK_CONTENT_V1: FACEBOOK_CONTENT_SCHEMA,
}


def response_schema_for(output_schema: AIOutputSchema) -> dict[str, Any]:
    """Return an isolated schema mapping so adapters cannot mutate the catalog."""

    try:
        return deepcopy(_SCHEMAS[output_schema])
    except KeyError as exc:
        raise ValueError(f"unsupported AI output schema: {output_schema}") from exc
