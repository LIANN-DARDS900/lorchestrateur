"""Registry for platform analytics adapters."""

from __future__ import annotations

from lorchestrateur.analytics.contracts import AnalyticsAdapter, AnalyticsUnavailableError


class AnalyticsRegistry:
    def __init__(self, adapters: tuple[AnalyticsAdapter, ...] = ()) -> None:
        self._adapters: dict[str, AnalyticsAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: AnalyticsAdapter) -> None:
        key = adapter.key.strip().lower()
        if not key:
            raise ValueError("analytics adapter key cannot be empty")
        if key in self._adapters:
            raise ValueError(f"analytics adapter already registered: {key}")
        self._adapters[key] = adapter

    def get(self, platform: str) -> AnalyticsAdapter:
        key = platform.strip().lower()
        try:
            return self._adapters[key]
        except KeyError as exc:
            raise AnalyticsUnavailableError(f"analytics unavailable for {key}") from exc

    def all(self) -> tuple[AnalyticsAdapter, ...]:
        return tuple(self._adapters.values())
