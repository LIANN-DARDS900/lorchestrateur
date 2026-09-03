"""Facebook and Instagram analytics with separate native metric contracts."""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import quote, urlencode

from lorchestrateur.analytics.contracts import (
    AnalyticsResponseError,
    AnalyticsResult,
    AnalyticsUnavailableError,
    MetricObservation,
)
from lorchestrateur.analytics.http import AnalyticsHTTPClient
from lorchestrateur.domain.analytics import MetricDefinition
from lorchestrateur.domain.publication import PublicationReceipt

INSTAGRAM_FIELDS = {
    "instagram.reach": "reach",
    "instagram.views": "views",
    "instagram.likes": "likes",
    "instagram.comments": "comments",
    "instagram.saves": "saved",
    "instagram.shares": "shares",
}


class InstagramAnalyticsAdapter:
    key = "instagram"
    adapter_name = "instagram-media-insights"
    adapter_version = "1"
    source_name = "meta.instagram.media_insights"

    def __init__(
        self,
        *,
        enabled: bool,
        access_token: str | None,
        base_url: str,
        timeout_seconds: float,
        http: AnalyticsHTTPClient | None = None,
    ) -> None:
        self._token = access_token
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http = http or AnalyticsHTTPClient()
        self.configured = bool(enabled and access_token)

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
            raise AnalyticsUnavailableError("Instagram analytics is not configured")
        native_names = ",".join(INSTAGRAM_FIELDS[item.key] for item in definitions)
        url = f"{self._base_url}/{quote(receipt.remote_id, safe='')}/insights?" + urlencode(
            {"metric": native_names}
        )
        payload = self._http.get_json(
            self.key,
            url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout_seconds=self._timeout_seconds,
        )
        data = payload.get("data")
        if not isinstance(data, list):
            raise AnalyticsResponseError("Instagram response is missing insight data")
        native_values = {}
        for item in data:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                raise AnalyticsResponseError("Instagram returned a malformed insight")
            value = _instagram_value(item)
            if value is not None:
                native_values[item["name"]] = value
        observations = []
        unavailable = []
        for definition in definitions:
            native = INSTAGRAM_FIELDS[definition.key]
            if native not in native_values:
                unavailable.append(definition.key)
            else:
                observations.append(
                    MetricObservation(definition.key, native_values[native], observed_at)
                )
        return AnalyticsResult(tuple(observations), tuple(unavailable))


class FacebookAnalyticsAdapter:
    key = "facebook"
    adapter_name = "facebook-post-fields"
    adapter_version = "1"
    source_name = "meta.facebook.post_fields"

    def __init__(
        self,
        *,
        enabled: bool,
        access_token: str | None,
        base_url: str,
        timeout_seconds: float,
        http: AnalyticsHTTPClient | None = None,
    ) -> None:
        self._token = access_token
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http = http or AnalyticsHTTPClient()
        self.configured = bool(enabled and access_token)

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
            raise AnalyticsUnavailableError("Facebook analytics is not configured")
        fields = "reactions.limit(0).summary(true),comments.limit(0).summary(true),shares"
        url = f"{self._base_url}/{quote(receipt.remote_id, safe='')}?" + urlencode(
            {"fields": fields}
        )
        payload = self._http.get_json(
            self.key,
            url,
            headers={"Authorization": f"Bearer {self._token}"},
            timeout_seconds=self._timeout_seconds,
        )
        values = {
            "facebook.reactions": _summary_count(payload.get("reactions")),
            "facebook.comments": _summary_count(payload.get("comments")),
            "facebook.shares": _shares_count(payload.get("shares")),
        }
        observations = []
        unavailable = []
        for definition in definitions:
            value = values[definition.key]
            if value is None:
                unavailable.append(definition.key)
            else:
                observations.append(MetricObservation(definition.key, value, observed_at))
        return AnalyticsResult(tuple(observations), tuple(unavailable))


def _non_negative_decimal(value) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise AnalyticsResponseError("Meta returned an invalid metric value")
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise AnalyticsResponseError("Meta returned an invalid metric value") from exc
    if not parsed.is_finite() or parsed < 0:
        raise AnalyticsResponseError("Meta returned an invalid metric value")
    return parsed


def _instagram_value(item: dict) -> Decimal | None:
    total = item.get("total_value")
    if isinstance(total, dict) and "value" in total:
        return _non_negative_decimal(total["value"])
    values = item.get("values")
    if not isinstance(values, list) or not values:
        return None
    last = values[-1]
    return _non_negative_decimal(last.get("value")) if isinstance(last, dict) else None


def _summary_count(value) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AnalyticsResponseError("Facebook returned malformed summary data")
    summary = value.get("summary")
    if not isinstance(summary, dict):
        return None
    return _non_negative_decimal(summary.get("total_count"))


def _shares_count(value) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise AnalyticsResponseError("Facebook returned malformed share data")
    return _non_negative_decimal(value.get("count"))
