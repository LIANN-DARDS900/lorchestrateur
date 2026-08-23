"""Small, shared standard-library HTTP boundary with bounded transient retries."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter, sleep
from types import MappingProxyType
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from lorchestrateur.ai.contracts import (
    AIProviderError,
    ProviderAuthenticationError,
    ProviderPermanentError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderTransientError,
)

LOGGER = logging.getLogger(__name__)
MAX_RESPONSE_BYTES = 2_000_000


@dataclass(frozen=True, slots=True)
class HTTPResponse:
    status_code: int
    body: bytes
    headers: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "headers",
            MappingProxyType({str(key).lower(): str(value) for key, value in self.headers.items()}),
        )


class JSONTransport(Protocol):
    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> HTTPResponse: ...


class UrllibJSONTransport:
    """HTTP transport that never includes response bodies in raised errors."""

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> HTTPResponse:
        request = Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers=dict(headers),
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ProviderResponseError("provider response exceeds the safe size limit")
                return HTTPResponse(response.status, body, dict(response.headers.items()))
        except HTTPError as exc:
            body = exc.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                body = b""
            headers = dict(exc.headers.items()) if exc.headers is not None else {}
            return HTTPResponse(exc.code, body, headers)
        except TimeoutError as exc:
            raise ProviderTimeoutError("provider request timed out") from exc
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderTimeoutError("provider request timed out") from exc
            raise ProviderTransientError("provider network request failed") from exc
        except OSError as exc:
            raise ProviderTransientError("provider network request failed") from exc


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_retries: int = 2
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 2.0
    max_retry_after_seconds: float = 5.0

    def __post_init__(self) -> None:
        if not isinstance(self.max_retries, int) or isinstance(self.max_retries, bool):
            raise ValueError("max_retries must be an integer")
        if not 0 <= self.max_retries <= 5:
            raise ValueError("max_retries must be between 0 and 5")
        for name in (
            "base_delay_seconds",
            "max_delay_seconds",
            "max_retry_after_seconds",
        ):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")


@dataclass(frozen=True, slots=True)
class JSONExecution:
    payload: Mapping[str, Any]
    requested_at: datetime
    latency_ms: int
    retry_count: int


class GovernedHTTPClient:
    """Classifies HTTP failures and retries only bounded transient conditions."""

    def __init__(
        self,
        *,
        transport: JSONTransport | None = None,
        retry_policy: RetryPolicy | None = None,
        sleeper: Callable[[float], None] = sleep,
        timer: Callable[[], float] = perf_counter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._transport = transport or UrllibJSONTransport()
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleeper = sleeper
        self._timer = timer
        self._clock = clock

    def post_json(
        self,
        provider: str,
        url: str,
        *,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> JSONExecution:
        requested_at = self._clock()
        started_at = self._timer()
        for retry_count in range(self._retry_policy.max_retries + 1):
            try:
                response = self._transport.post_json(
                    url,
                    headers=headers,
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                )
                error = _classify_status(provider, response)
                if error is not None:
                    raise error
                decoded = json.loads(response.body.decode("utf-8"))
                if not isinstance(decoded, Mapping):
                    raise ProviderResponseError("provider returned a non-object JSON response")
                latency_ms = max(0, int((self._timer() - started_at) * 1_000))
                return JSONExecution(
                    payload=MappingProxyType(dict(decoded)),
                    requested_at=requested_at,
                    latency_ms=latency_ms,
                    retry_count=retry_count,
                )
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProviderResponseError("provider returned invalid JSON") from exc
            except AIProviderError as exc:
                if not exc.retryable or retry_count >= self._retry_policy.max_retries:
                    exc.retry_count = retry_count
                    raise
                delay = self._retry_delay(exc, retry_count)
                LOGGER.info(
                    "retrying transient AI provider request",
                    extra={
                        "provider": provider,
                        "error_classification": exc.classification,
                        "retry_count": retry_count + 1,
                    },
                )
                self._sleeper(delay)
        raise AssertionError("bounded provider retry loop exited unexpectedly")

    def _retry_delay(self, error: AIProviderError, retry_count: int) -> float:
        if isinstance(error, ProviderRateLimitError) and error.retry_after_seconds is not None:
            return min(
                max(0.0, error.retry_after_seconds),
                self._retry_policy.max_retry_after_seconds,
            )
        return min(
            self._retry_policy.base_delay_seconds * (2**retry_count),
            self._retry_policy.max_delay_seconds,
        )


def _classify_status(provider: str, response: HTTPResponse) -> AIProviderError | None:
    status = response.status_code
    if 200 <= status < 300:
        return None
    if status in {401, 403}:
        return ProviderAuthenticationError(f"{provider} rejected provider credentials")
    if status == 429:
        return ProviderRateLimitError(
            f"{provider} rate limit was reached",
            retry_after_seconds=_retry_after(response.headers),
        )
    if status == 408:
        return ProviderTimeoutError(f"{provider} request timed out")
    if status in {500, 502, 503, 504}:
        return ProviderTransientError(f"{provider} returned a transient server failure")
    if 400 <= status < 500:
        return ProviderPermanentError(f"{provider} rejected the request")
    if status >= 500:
        return ProviderTransientError(f"{provider} returned a server failure")
    return ProviderPermanentError(f"{provider} returned an unexpected HTTP status")


def _retry_after(headers: Mapping[str, str]) -> float | None:
    raw_value = headers.get("retry-after")
    if raw_value is None:
        return None
    try:
        value = float(raw_value.strip())
    except ValueError:
        return None
    return value if value >= 0 else None
