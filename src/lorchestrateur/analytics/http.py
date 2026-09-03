"""Bounded, secret-safe GET boundary for analytics adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from lorchestrateur.ai.providers.http import MAX_RESPONSE_BYTES, HTTPResponse
from lorchestrateur.analytics.contracts import (
    AnalyticsAuthenticationError,
    AnalyticsPermanentError,
    AnalyticsPermissionError,
    AnalyticsRateLimitError,
    AnalyticsResponseError,
    AnalyticsTransientError,
)


class AnalyticsGETTransport(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HTTPResponse: ...


class UrllibAnalyticsGETTransport:
    def get(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> HTTPResponse:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise AnalyticsResponseError("analytics response exceeds the size limit")
                return HTTPResponse(response.status, body, dict(response.headers.items()))
        except HTTPError as exc:
            body = exc.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                body = b""
            return HTTPResponse(
                exc.code,
                body,
                dict(exc.headers.items()) if exc.headers is not None else {},
            )
        except (TimeoutError, URLError, OSError) as exc:
            raise AnalyticsTransientError("analytics network request failed") from exc


@dataclass(frozen=True, slots=True)
class AnalyticsHTTPClient:
    transport: AnalyticsGETTransport = UrllibAnalyticsGETTransport()

    def get_json(
        self,
        platform: str,
        url: str,
        *,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        response = self.transport.get(url, headers=headers, timeout_seconds=timeout_seconds)
        status = response.status_code
        if status == 401:
            raise AnalyticsAuthenticationError(f"{platform} rejected analytics credentials")
        if status == 403:
            raise AnalyticsPermissionError(f"{platform} denied analytics permission")
        if status == 429:
            raise AnalyticsRateLimitError(
                f"{platform} analytics rate limit reached",
                retry_after_seconds=_retry_after(response.headers),
            )
        if status in {408, 500, 502, 503, 504}:
            raise AnalyticsTransientError(f"{platform} returned a transient analytics failure")
        if 400 <= status < 500:
            raise AnalyticsPermanentError(f"{platform} rejected the analytics request")
        if status < 200 or status >= 300:
            raise AnalyticsPermanentError(f"{platform} returned an unexpected status")
        try:
            decoded = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AnalyticsResponseError(f"{platform} returned malformed JSON") from exc
        if not isinstance(decoded, Mapping):
            raise AnalyticsResponseError(f"{platform} returned a non-object response")
        return decoded


def _retry_after(headers: Mapping[str, str]) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None
