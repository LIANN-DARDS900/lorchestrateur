"""X API v2 publisher for approved single posts and resumable threads."""

from __future__ import annotations

from lorchestrateur.domain.platform_content import PlatformContentRecord
from lorchestrateur.domain.publication import MediaAsset, PublicationReceipt, PublicationRequest
from lorchestrateur.platforms.x import XContentV1
from lorchestrateur.publishing.contracts import (
    PreparedItem,
    PreparedPublication,
    PublicationPermanentError,
    PublicationUnavailableError,
    PublishedItem,
    ReconciliationResult,
)
from lorchestrateur.publishing.http import PublicationHTTPClient


class XPublisher:
    key = "x"
    adapter_name = "x-api-v2"
    adapter_version = "1"

    def __init__(
        self,
        *,
        enabled: bool,
        access_token: str | None,
        base_url: str,
        timeout_seconds: float = 20.0,
        http: PublicationHTTPClient | None = None,
    ) -> None:
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http = http or PublicationHTTPClient()
        self.configured = bool(enabled and access_token)
        self.destination_label = "Compte X configuré" if self.configured else "Non configuré"

    def prepare(
        self, content: PlatformContentRecord, assets: tuple[MediaAsset, ...]
    ) -> PreparedPublication:
        del assets
        if not isinstance(content.payload, XContentV1):
            raise PublicationPermanentError("X content payload is not supported")
        return PreparedPublication(
            platform=self.key,
            items=tuple(
                PreparedItem(post.order, "post", {"text": post.text})
                for post in content.payload.posts
            ),
            destination_label=self.destination_label,
        )

    def publish_item(
        self,
        publication: PublicationRequest,
        item: PreparedItem,
        *,
        parent_remote_id: str | None,
    ) -> PublishedItem:
        del publication
        if not self.configured or not self._access_token:
            raise PublicationUnavailableError("X publishing is not configured")
        payload = dict(item.payload)
        if parent_remote_id is not None:
            payload["reply"] = {"in_reply_to_tweet_id": parent_remote_id}
        response = self._http.post_json(
            self.key,
            f"{self._base_url}/2/tweets",
            headers={
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=self._timeout_seconds,
        )
        data = response.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("id"), str):
            raise PublicationPermanentError("X returned a malformed publication receipt")
        return PublishedItem(remote_id=data["id"])

    def reconcile(
        self,
        publication: PublicationRequest,
        receipts: tuple[PublicationReceipt, ...],
    ) -> ReconciliationResult:
        del publication, receipts
        return ReconciliationResult(False)
