"""Platform contracts, built-in definitions, and registration."""

from lorchestrateur.platforms.blog import BlogContentV1, BlogSectionV1
from lorchestrateur.platforms.builtins import create_default_registry
from lorchestrateur.platforms.contracts import (
    ContentFieldRule,
    Platform,
    PlatformContent,
    SchemaPlatform,
)
from lorchestrateur.platforms.facebook import FacebookContentV1
from lorchestrateur.platforms.instagram import (
    InstagramBeatV1,
    InstagramCarouselV1,
    InstagramImagePostV1,
    InstagramReelV1,
    InstagramSlideV1,
)
from lorchestrateur.platforms.registry import (
    DuplicatePlatformError,
    PlatformNotRegisteredError,
    PlatformRegistry,
)
from lorchestrateur.platforms.x import XContentV1, XFormat, XPostV1

__all__ = [
    "ContentFieldRule",
    "BlogContentV1",
    "BlogSectionV1",
    "DuplicatePlatformError",
    "Platform",
    "PlatformContent",
    "PlatformNotRegisteredError",
    "PlatformRegistry",
    "SchemaPlatform",
    "FacebookContentV1",
    "InstagramBeatV1",
    "InstagramCarouselV1",
    "InstagramImagePostV1",
    "InstagramReelV1",
    "InstagramSlideV1",
    "XContentV1",
    "XFormat",
    "XPostV1",
    "create_default_registry",
]
