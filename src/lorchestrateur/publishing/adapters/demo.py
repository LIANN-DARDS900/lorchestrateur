"""Deterministic no-network publisher used by demos and automated tests."""

from __future__ import annotations

from lorchestrateur.domain.platform_content import PlatformContentRecord
from lorchestrateur.domain.publication import MediaAsset, PublicationReceipt, PublicationRequest
from lorchestrateur.publishing.contracts import (
    PreparedItem,
    PreparedPublication,
    PublishedItem,
    Publisher,
    ReconciliationResult,
)


class DemoPublisher:
    adapter_version = "1"
    configured = True

    def __init__(self, delegate: Publisher) -> None:
        self._delegate = delegate
        self.key = delegate.key
        self.adapter_name = f"demo-{delegate.key}"
        self.destination_label = f"Destination de démonstration · {delegate.key.title()}"

    def prepare(
        self, content: PlatformContentRecord, assets: tuple[MediaAsset, ...]
    ) -> PreparedPublication:
        prepared = self._delegate.prepare(content, assets)
        return PreparedPublication(
            platform=prepared.platform,
            items=prepared.items,
            warnings=prepared.warnings,
            destination_label=self.destination_label,
        )

    def publish_item(
        self,
        publication: PublicationRequest,
        item: PreparedItem,
        *,
        parent_remote_id: str | None,
    ) -> PublishedItem:
        del parent_remote_id
        return PublishedItem(
            remote_id=f"demo-{self.key}-{publication.id}-{item.index}",
            status="delivered_demo",
            metadata={"external_delivery": False},
        )

    def reconcile(
        self,
        publication: PublicationRequest,
        receipts: tuple[PublicationReceipt, ...],
    ) -> ReconciliationResult:
        if not receipts:
            return ReconciliationResult(False)
        return ReconciliationResult(True, remote_id=receipts[-1].remote_id)
