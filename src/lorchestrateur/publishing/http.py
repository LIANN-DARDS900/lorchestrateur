"""Secret-safe HTTP boundary for live publication adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lorchestrateur.ai.contracts import ProviderTimeoutError, ProviderTransientError
from lorchestrateur.ai.providers.http import JSONTransport, UrllibJSONTransport
from lorchestrateur.publishing.contracts import (
    PublicationAmbiguousOutcomeError,
    PublicationAuthenticationError,
    PublicationPermanentError,
    PublicationPermissionError,
    PublicationRateLimitError,
    PublicationTransientError,
    PublicationValidationError,
)


@dataclass(frozen=True, slots=True)
class PublicationHTTPClient:
    transport: JSONTransport = UrllibJSONTransport()

    def post_json(
        self,
        platform: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        try:
            response = self.transport.post_json(
                url,
                headers=headers,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        except (ProviderTimeoutError, ProviderTransientError) as exc:
            raise PublicationAmbiguousOutcomeError(
                f"{platform} publication outcome could not be confirmed"
            ) from exc
        status = response.status_code
        if status == 401:
            raise PublicationAuthenticationError(f"{platform} rejected publication credentials")
        if status == 403:
            raise PublicationPermissionError(f"{platform} denied publication permission")
        if status == 429:
            raise PublicationRateLimitError(
                f"{platform} publication rate limit reached",
                retry_after_seconds=_retry_after(response.headers),
            )
        if status in {408, 500, 502, 503, 504}:
            raise PublicationTransientError(f"{platform} returned a transient publication failure")
        if 400 <= status < 500:
            raise PublicationValidationError(f"{platform} rejected publication payload")
        if status < 200 or status >= 300:
            raise PublicationPermanentError(f"{platform} returned an unexpected publication status")
        try:
            decoded = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublicationPermanentError(f"{platform} returned malformed JSON") from exc
        if not isinstance(decoded, Mapping):
            raise PublicationPermanentError(f"{platform} returned a non-object response")
        return decoded


def _retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None
