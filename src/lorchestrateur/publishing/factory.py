"""Composition of demo or explicitly configured live publication adapters."""

from __future__ import annotations

from lorchestrateur.config import Settings
from lorchestrateur.publishing.adapters.blog import BlogExportPublisher
from lorchestrateur.publishing.adapters.demo import DemoPublisher
from lorchestrateur.publishing.adapters.facebook import FacebookPublisher
from lorchestrateur.publishing.adapters.instagram import InstagramPublisher
from lorchestrateur.publishing.adapters.x import XPublisher
from lorchestrateur.publishing.registry import PublishingRegistry


def create_publishing_registry(settings: Settings) -> PublishingRegistry:
    base_publishers = (
        BlogExportPublisher(
            enabled=settings.blog_publishing_enabled,
            export_directory=settings.blog_export_directory,
        ),
        XPublisher(
            enabled=settings.x_publishing_enabled,
            access_token=settings.x_access_token,
            base_url=settings.x_api_base_url,
        ),
        InstagramPublisher(
            enabled=settings.instagram_publishing_enabled,
            account_id=settings.instagram_business_account_id,
            access_token=settings.instagram_access_token,
            base_url=settings.meta_graph_base_url,
        ),
        FacebookPublisher(
            enabled=settings.facebook_publishing_enabled,
            page_id=settings.meta_page_id,
            access_token=settings.meta_page_access_token,
            base_url=settings.meta_graph_base_url,
        ),
    )
    if settings.publishing_adapter_mode == "demo":
        return PublishingRegistry(tuple(DemoPublisher(item) for item in base_publishers))
    return PublishingRegistry(base_publishers)
