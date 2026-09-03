"""Platform registry used by the orchestration core instead of condition chains."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from lorchestrateur.domain.platform_content import PlatformPayload
from lorchestrateur.domain.validation import ValidationResult
from lorchestrateur.platforms.contracts import Platform, PlatformContent


class DuplicatePlatformError(ValueError):
    pass


class PlatformNotRegisteredError(LookupError):
    pass


class PlatformRegistry:
    def __init__(self, platforms: Iterable[Platform] = ()) -> None:
        self._platforms: dict[str, Platform] = {}
        for platform in platforms:
            self.register(platform)

    def register(self, platform: Platform) -> None:
        key = platform.key.strip().lower()
        if not key:
            raise ValueError("platform key cannot be empty")
        if key in self._platforms:
            raise DuplicatePlatformError(f"platform already registered: {key}")
        self._platforms[key] = platform

    def get(self, key: str) -> Platform:
        normalized_key = key.strip().lower()
        try:
            return self._platforms[normalized_key]
        except KeyError as exc:
            raise PlatformNotRegisteredError(
                f"platform is not registered: {normalized_key}"
            ) from exc

    def validate(self, content: PlatformContent) -> ValidationResult:
        return self.get(content.platform).validate(content)

    def parse_payload(
        self, platform: str, payload: Mapping[str, Any]
    ) -> PlatformPayload:
        return self.get(platform).parse_payload(payload)

    def keys(self) -> tuple[str, ...]:
        return tuple(self._platforms)
