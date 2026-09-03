"""Facebook Page publisher behind the common publication contract."""

from __future__ import annotations

from urllib.parse import urlparse

from lorchestrateur.domain.platform_content import PlatformContentRecord
from lorchestrateur.domain.publication import MediaAsset, PublicationReceipt, PublicationRequest
from lorchestrateur.platforms.facebook import FacebookContentV1
from lorchestrateur.publishing.contracts import (
    PreparedItem,
    PreparedPublication,
    PublicationPermanentError,
    PublicationUnavailableError,
    PublishedItem,
    ReconciliationResult,
)
from lorchestrateur.publishing.http import PublicationHTTPClient


class FacebookPublisher:
    key = "facebook"
    adapter_name = "meta-pages-api"
    adapter_version = "1"

    def __init__(
        self,
        *,
        enabled: bool,
        page_id: str | None,
        access_token: str | None,
        base_url: str,
        timeout_seconds: float = 20.0,
        http: PublicationHTTPClient | None = None,
    ) -> None:
        self._page_id = page_id
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http = http or PublicationHTTPClient()
        self.configured = bool(enabled and page_id and access_token)
        self.destination_label = f"Page {page_id}" if self.configured else "Non configuré"

    def prepare(
        self, content: PlatformContentRecord, assets: tuple[MediaAsset, ...]
    ) -> PreparedPublication:
        del assets
        payload = content.payload
        if not isinstance(payload, FacebookContentV1):
            raise PublicationPermanentError("Facebook content payload is not supported")
        message = "\n\n".join(item for item in (payload.opening, payload.body, payload.cta) if item)
        body: dict[str, str] = {"message": message}
        if payload.link_context_recommendation and _is_https_url(
            payload.link_context_recommendation
        ):
            body["link"] = payload.link_context_recommendation
        return PreparedPublication(
            platform=self.key,
            items=(PreparedItem(1, "page_post", body),),
            destination_label=self.destination_label,
        )

    def publish_item(
        self,
        publication: PublicationRequest,
        item: PreparedItem,
        *,
        parent_remote_id: str | None,
    ) -> PublishedItem:
        del publication, parent_remote_id
        if not self.configured or not self._page_id or not self._access_token:
            raise PublicationUnavailableError("Facebook publishing is not configured")
        response = self._http.post_json(
            self.key,
            f"{self._base_url}/{self._page_id}/feed",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            payload=item.payload,
            timeout_seconds=self._timeout_seconds,
        )
        remote_id = response.get("id")
        if not isinstance(remote_id, str) or not remote_id:
            raise PublicationPermanentError("Facebook returned a malformed publication receipt")
        return PublishedItem(remote_id=remote_id)

    def reconcile(
        self,
        publication: PublicationRequest,
        receipts: tuple[PublicationReceipt, ...],
    ) -> ReconciliationResult:
        del publication, receipts
        return ReconciliationResult(False)


def _is_https_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)
