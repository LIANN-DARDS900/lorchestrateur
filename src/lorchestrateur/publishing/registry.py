"""Registry for platform publication adapters."""

from __future__ import annotations

from lorchestrateur.publishing.contracts import Publisher


class PublishingRegistry:
    def __init__(self, publishers: tuple[Publisher, ...] = ()) -> None:
        self._publishers: dict[str, Publisher] = {}
        for publisher in publishers:
            self.register(publisher)

    def register(self, publisher: Publisher) -> None:
        key = publisher.key.strip().lower()
        if not key:
            raise ValueError("publisher key cannot be empty")
        if key in self._publishers:
            raise ValueError(f"publisher already registered: {key}")
        self._publishers[key] = publisher

    def get(self, platform: str) -> Publisher:
        key = platform.strip().lower()
        try:
            return self._publishers[key]
        except KeyError as exc:
            raise KeyError(f"no publishing adapter registered for {key}") from exc

    def all(self) -> tuple[Publisher, ...]:
        return tuple(self._publishers.values())
