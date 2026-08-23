"""Instagram content publisher with an explicit media-package boundary."""

from __future__ import annotations

from lorchestrateur.domain.platform_content import PlatformContentRecord
from lorchestrateur.domain.publication import (
    MediaAsset,
    MediaAssetType,
    PublicationReceipt,
    PublicationRequest,
)
from lorchestrateur.platforms.instagram import (
    InstagramCarouselV1,
    InstagramImagePostV1,
    InstagramReelV1,
)
from lorchestrateur.publishing.contracts import (
    PreparedItem,
    PreparedPublication,
    PublicationPermanentError,
    PublicationUnavailableError,
    PublicationValidationError,
    PublishedItem,
    ReconciliationResult,
)
from lorchestrateur.publishing.http import PublicationHTTPClient


class InstagramPublisher:
    key = "instagram"
    adapter_name = "instagram-graph-api"
    adapter_version = "1"

    def __init__(
        self,
        *,
        enabled: bool,
        account_id: str | None,
        access_token: str | None,
        base_url: str,
        timeout_seconds: float = 30.0,
        http: PublicationHTTPClient | None = None,
    ) -> None:
        self._account_id = account_id
        self._access_token = access_token
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._http = http or PublicationHTTPClient()
        self.configured = bool(enabled and account_id and access_token)
        self.destination_label = (
            f"Compte Instagram {account_id}" if self.configured else "Non configuré"
        )

    def prepare(
        self, content: PlatformContentRecord, assets: tuple[MediaAsset, ...]
    ) -> PreparedPublication:
        payload = content.payload
        ordered = tuple(sorted(assets, key=lambda asset: asset.order))
        if isinstance(payload, InstagramCarouselV1):
            if len(ordered) != len(payload.slides) or any(
                asset.media_type is not MediaAssetType.IMAGE for asset in ordered
            ):
                raise PublicationValidationError(
                    f"Instagram carousel requires {len(payload.slides)} ordered images"
                )
            item_payload = {
                "caption": _caption(payload.caption, payload.cta),
                "media_urls": [asset.source_url for asset in ordered],
            }
            kind = "carousel"
        elif isinstance(payload, InstagramReelV1):
            if len(ordered) != 1 or ordered[0].media_type is not MediaAssetType.VIDEO:
                raise PublicationValidationError("Instagram reel requires one video asset")
            item_payload = {
                "caption": _caption(payload.caption, payload.cta),
                "video_url": ordered[0].source_url,
            }
            kind = "reel"
        elif isinstance(payload, InstagramImagePostV1):
            if len(ordered) != 1 or ordered[0].media_type is not MediaAssetType.IMAGE:
                raise PublicationValidationError("Instagram image post requires one image asset")
            item_payload = {
                "caption": _caption(payload.caption, payload.cta),
                "image_url": ordered[0].source_url,
            }
            kind = "image"
        else:
            raise PublicationPermanentError("Instagram content payload is not supported")
        return PreparedPublication(
            platform=self.key,
            items=(PreparedItem(1, kind, item_payload),),
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
        if not self.configured or not self._account_id or not self._access_token:
            raise PublicationUnavailableError("Instagram publishing is not configured")
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }
        if item.kind == "carousel":
            child_ids = [
                self._create_container(
                    headers,
                    {"image_url": url, "is_carousel_item": True},
                )
                for url in item.payload["media_urls"]
            ]
            container_id = self._create_container(
                headers,
                {
                    "media_type": "CAROUSEL",
                    "children": child_ids,
                    "caption": item.payload["caption"],
                },
            )
        elif item.kind == "reel":
            container_id = self._create_container(
                headers,
                {
                    "media_type": "REELS",
                    "video_url": item.payload["video_url"],
                    "caption": item.payload["caption"],
                },
            )
        else:
            container_id = self._create_container(headers, item.payload)
        response = self._http.post_json(
            self.key,
            f"{self._base_url}/{self._account_id}/media_publish",
            headers=headers,
            payload={"creation_id": container_id},
            timeout_seconds=self._timeout_seconds,
        )
        remote_id = response.get("id")
        if not isinstance(remote_id, str) or not remote_id:
            raise PublicationPermanentError("Instagram returned a malformed publication receipt")
        return PublishedItem(remote_id=remote_id)

    def reconcile(
        self,
        publication: PublicationRequest,
        receipts: tuple[PublicationReceipt, ...],
    ) -> ReconciliationResult:
        del publication, receipts
        return ReconciliationResult(False)

    def _create_container(self, headers: dict[str, str], payload: dict) -> str:
        response = self._http.post_json(
            self.key,
            f"{self._base_url}/{self._account_id}/media",
            headers=headers,
            payload=payload,
            timeout_seconds=self._timeout_seconds,
        )
        container_id = response.get("id")
        if not isinstance(container_id, str) or not container_id:
            raise PublicationPermanentError("Instagram returned a malformed media container")
        return container_id


def _caption(caption: str, cta: str | None) -> str:
    return "\n\n".join(item for item in (caption, cta) if item)
