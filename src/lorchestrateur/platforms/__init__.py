"""Platform contracts, built-in definitions, and registration."""

from lorchestrateur.platforms.builtins import create_default_registry
from lorchestrateur.platforms.contracts import (
    ContentFieldRule,
    Platform,
    PlatformContent,
    SchemaPlatform,
)
from lorchestrateur.platforms.registry import (
    DuplicatePlatformError,
    PlatformNotRegisteredError,
    PlatformRegistry,
)

__all__ = [
    "ContentFieldRule",
    "DuplicatePlatformError",
    "Platform",
    "PlatformContent",
    "PlatformNotRegisteredError",
    "PlatformRegistry",
    "SchemaPlatform",
    "create_default_registry",
]

