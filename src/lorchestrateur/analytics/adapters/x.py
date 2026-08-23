"""X public post metrics adapter using known publication receipt IDs."""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import quote

from lorchestrateur.analytics.contracts import (
    AnalyticsResponseError,
    AnalyticsResult,
    AnalyticsUnavailableError,
    MetricObservation,
)
from lorchestrateur.analytics.http import AnalyticsHTTPClient
from lorchestrateur.domain.analytics import MetricDefinition
from lorchestrateur.domain.publication import PublicationReceipt

X_PUBLIC_FIELDS = {
    "x.impressions": "impression_count",
    "x.likes": "like_count",
    "x.replies": "reply_count",
    "x.reposts": "retweet_count",
    "x.quotes": "quote_count",
    "x.bookmarks": "bookmark_count",
}


class XAnalyticsAdapter:
    key = "x"
    adapter_name = "x-public-metrics-v2"
    adapter_version = "1"
    source_name = "x.api.v2.public_metrics"

    def __init__(
        self,
        *,
        enabled: bool,
        bearer_token: str | None,
        base_url: str,
        timeout_seconds: float,
        http: AnalyticsHTTPClient | None = None,
    ) -> None:
        self._token = bearer_token
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http = http or AnalyticsHTTPClient()
        self.configured = bool(enabled and bearer_token)

    def collect(
        self,
        receipt: PublicationReceipt,
        definitions: tuple[MetricDefinition, ...],
        *,
        observed_at,
        collection_index: int,
    ) -> AnalyticsResult:
        del collection_index
        if not self.configured or not self._token:
            raise AnalyticsUnavailableError("X analytics is not configured")
        remote_id = quote(receipt.remote_id, safe="")
        payload = self._http.get_json(
            self.key,
            f"{self._base_url}/2/tweets/{remote_id}?tweet.fields=public_metrics",
            headers={"Authorization": f"Bearer {self._token}"},
            timeout_seconds=self._timeout_seconds,
        )
        data = payload.get("data")
        metrics = data.get("public_metrics") if isinstance(data, dict) else None
        if not isinstance(metrics, dict):
            raise AnalyticsResponseError("X response is missing public metrics")
        observations = []
        unavailable = []
        for definition in definitions:
            field = X_PUBLIC_FIELDS[definition.key]
            value = metrics.get(field)
            if value is None:
                unavailable.append(definition.key)
            elif isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                observations.append(MetricObservation(definition.key, Decimal(value), observed_at))
            else:
                raise AnalyticsResponseError("X returned an invalid metric value")
        return AnalyticsResult(tuple(observations), tuple(unavailable))
