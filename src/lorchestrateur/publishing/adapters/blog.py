"""Safe local Markdown export implementing the BlogPublisher boundary."""

from __future__ import annotations

from pathlib import Path

from lorchestrateur.domain.platform_content import PlatformContentRecord
from lorchestrateur.domain.publication import MediaAsset, PublicationReceipt, PublicationRequest
from lorchestrateur.platforms.blog import BlogContentV1
from lorchestrateur.publishing.contracts import (
    PreparedItem,
    PreparedPublication,
    PublicationPermanentError,
    PublicationUnavailableError,
    PublishedItem,
    ReconciliationResult,
)


class BlogExportPublisher:
    key = "blog"
    adapter_name = "local-markdown-export"
    adapter_version = "1"

    def __init__(self, *, enabled: bool, export_directory: str | Path) -> None:
        self._export_directory = Path(export_directory)
        self.configured = enabled
        self.destination_label = f"Export local · {self._export_directory.name}"

    def prepare(
        self, content: PlatformContentRecord, assets: tuple[MediaAsset, ...]
    ) -> PreparedPublication:
        del assets
        payload = content.payload
        if not isinstance(payload, BlogContentV1):
            raise PublicationPermanentError("Blog content payload is not supported")
        markdown = _to_markdown(payload)
        return PreparedPublication(
            platform=self.key,
            items=(
                PreparedItem(
                    1,
                    "markdown_export",
                    {"slug": payload.slug_suggestion, "markdown": markdown},
                ),
            ),
            warnings=("Livraison locale : ce reçu ne représente pas une mise en ligne.",),
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
        if not self.configured:
            raise PublicationUnavailableError("Blog export is not enabled")
        self._export_directory.mkdir(parents=True, exist_ok=True)
        filename = f"{item.payload['slug']}-{publication.id}.md"
        destination = (self._export_directory / filename).resolve()
        export_root = self._export_directory.resolve()
        if export_root not in destination.parents:
            raise PublicationPermanentError("Blog export destination escaped its root")
        destination.write_text(str(item.payload["markdown"]), encoding="utf-8")
        return PublishedItem(
            remote_id=f"export:{filename}",
            status="exported",
            metadata={"filename": filename},
        )

    def reconcile(
        self,
        publication: PublicationRequest,
        receipts: tuple[PublicationReceipt, ...],
    ) -> ReconciliationResult:
        del receipts
        pattern = f"*-{publication.id}.md"
        matches = tuple(self._export_directory.glob(pattern))
        return ReconciliationResult(
            confirmed=bool(matches),
            remote_id=f"export:{matches[0].name}" if matches else None,
        )


def _to_markdown(payload: BlogContentV1) -> str:
    parts = [
        "---",
        f'title: "{payload.title.replace(chr(34), chr(39))}"',
        f'seo_title: "{payload.seo_title.replace(chr(34), chr(39))}"',
        f'meta_description: "{payload.meta_description.replace(chr(34), chr(39))}"',
        "---",
        "",
        f"# {payload.title}",
        "",
        payload.introduction,
    ]
    for section in payload.sections:
        parts.extend(("", f"## {section.heading}", "", section.body))
    parts.extend(("", "## Conclusion", "", payload.conclusion))
    if payload.cta:
        parts.extend(("", payload.cta))
    parts.extend(("", f"_Sources : {', '.join(payload.source_ids)}_", ""))
    return "\n".join(parts)
