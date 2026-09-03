"""Composition of deterministic demo or explicitly configured live analytics."""

from __future__ import annotations

from lorchestrateur.analytics.adapters.blog import BlogAnalyticsAdapter
from lorchestrateur.analytics.adapters.demo import DemoAnalyticsAdapter
from lorchestrateur.analytics.adapters.meta import (
    FacebookAnalyticsAdapter,
    InstagramAnalyticsAdapter,
)
from lorchestrateur.analytics.adapters.x import XAnalyticsAdapter
from lorchestrateur.analytics.registry import AnalyticsRegistry
from lorchestrateur.config import Settings


def create_analytics_registry(settings: Settings) -> AnalyticsRegistry:
    if settings.analytics_adapter_mode == "demo":
        return AnalyticsRegistry(
            (
                BlogAnalyticsAdapter(),
                DemoAnalyticsAdapter("x"),
                DemoAnalyticsAdapter("instagram"),
                DemoAnalyticsAdapter("facebook"),
            )
        )
    return AnalyticsRegistry(
        (
            BlogAnalyticsAdapter(),
            XAnalyticsAdapter(
                enabled=settings.x_analytics_enabled,
                bearer_token=settings.x_analytics_bearer_token,
                base_url=settings.x_api_base_url,
                timeout_seconds=settings.analytics_timeout_seconds,
            ),
            InstagramAnalyticsAdapter(
                enabled=settings.meta_analytics_enabled,
                access_token=settings.meta_analytics_access_token,
                base_url=settings.meta_graph_base_url,
                timeout_seconds=settings.analytics_timeout_seconds,
            ),
            FacebookAnalyticsAdapter(
                enabled=settings.meta_analytics_enabled,
                access_token=settings.meta_analytics_access_token,
                base_url=settings.meta_graph_base_url,
                timeout_seconds=settings.analytics_timeout_seconds,
            ),
        )
    )
