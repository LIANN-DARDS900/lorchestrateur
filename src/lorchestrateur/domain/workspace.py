"""Typed workspace profiles and explicitly approved reusable knowledge."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime

from lorchestrateur.domain.content import EvidenceStatus, SourceType

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SUPPORTED_PLATFORMS = frozenset({"blog", "x", "instagram", "facebook"})


def _text(name: str, value: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} cannot be empty")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{name} cannot exceed {maximum} characters")
    return normalized


def _optional_text(name: str, value: str | None, *, maximum: int) -> str | None:
    if value is None or not value.strip():
        return None
    return _text(name, value, maximum=maximum)


def _aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include timezone information")


def _unique_text(values: tuple[str, ...], *, maximum: int) -> tuple[str, ...]:
    normalized = tuple(_text("list item", item, maximum=maximum) for item in values)
    return tuple(dict.fromkeys(normalized))


@dataclass(frozen=True, slots=True)
class WorkspaceProfile:
    id: str
    display_name: str
    slug: str
    website_url: str | None
    description: str | None
    default_audience: str
    default_objective: str
    default_tone: str
    default_cta: str | None
    default_topic_category: str
    default_platforms: tuple[str, ...]
    business_constraints: tuple[str, ...]
    forbidden_claims: tuple[str, ...]
    uncertain_claims: tuple[str, ...]
    reuse_approved_knowledge: bool
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text("workspace id", self.id, maximum=100))
        object.__setattr__(
            self, "display_name", _text("display name", self.display_name, maximum=120)
        )
        normalized_slug = _text("slug", self.slug, maximum=80).lower()
        if not _SLUG.fullmatch(normalized_slug):
            raise ValueError("slug must contain lowercase letters, numbers, and hyphens")
        object.__setattr__(self, "slug", normalized_slug)
        object.__setattr__(
            self, "website_url", _optional_text("website URL", self.website_url, maximum=500)
        )
        object.__setattr__(
            self, "description", _optional_text("description", self.description, maximum=2000)
        )
        for name, maximum in (
            ("default_audience", 500),
            ("default_objective", 500),
            ("default_tone", 200),
            ("default_topic_category", 120),
        ):
            object.__setattr__(self, name, _text(name, getattr(self, name), maximum=maximum))
        object.__setattr__(
            self, "default_cta", _optional_text("default CTA", self.default_cta, maximum=500)
        )
        platforms = tuple(dict.fromkeys(item.strip().lower() for item in self.default_platforms))
        if not platforms or set(platforms) - _SUPPORTED_PLATFORMS:
            raise ValueError("default platforms must use supported platform identifiers")
        object.__setattr__(self, "default_platforms", platforms)
        for name in ("business_constraints", "forbidden_claims", "uncertain_claims"):
            object.__setattr__(self, name, _unique_text(tuple(getattr(self, name)), maximum=500))
        if self.revision < 1:
            raise ValueError("workspace revision must be positive")
        _aware("created_at", self.created_at)
        _aware("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("workspace update cannot precede creation")

    def revised(self, *, now: datetime, **changes: object) -> WorkspaceProfile:
        return replace(self, **changes, revision=self.revision + 1, updated_at=now)


@dataclass(frozen=True, slots=True)
class WorkspaceKnowledgeItem:
    id: str
    workspace_id: str
    title: str
    url: str | None
    source_type: SourceType
    relevant_excerpt: str
    evidence_status: EvidenceStatus
    reusable: bool
    active: bool
    origin_job_id: str | None
    origin_source_id: str | None
    revision: int
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        for name in ("id", "workspace_id"):
            object.__setattr__(self, name, _text(name, getattr(self, name), maximum=100))
        object.__setattr__(self, "title", _text("title", self.title, maximum=300))
        object.__setattr__(self, "url", _optional_text("URL", self.url, maximum=500))
        object.__setattr__(
            self,
            "relevant_excerpt",
            _text("relevant excerpt", self.relevant_excerpt, maximum=5000),
        )
        if not isinstance(self.source_type, SourceType):
            raise ValueError("source_type must be a SourceType")
        if not isinstance(self.evidence_status, EvidenceStatus):
            raise ValueError("evidence_status must be an EvidenceStatus")
        for name in ("origin_job_id", "origin_source_id"):
            object.__setattr__(self, name, _optional_text(name, getattr(self, name), maximum=100))
        if self.revision < 1:
            raise ValueError("knowledge revision must be positive")
        _aware("created_at", self.created_at)
        _aware("updated_at", self.updated_at)
        if self.updated_at < self.created_at:
            raise ValueError("knowledge update cannot precede creation")

    @property
    def eligible_for_reuse(self) -> bool:
        return self.active and self.reusable and self.evidence_status is EvidenceStatus.REVIEWED

    def revised(self, *, now: datetime, **changes: object) -> WorkspaceKnowledgeItem:
        return replace(self, **changes, revision=self.revision + 1, updated_at=now)
