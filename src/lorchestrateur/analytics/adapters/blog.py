"""Explicit unavailable boundary for local Markdown exports."""

from __future__ import annotations

from lorchestrateur.analytics.contracts import AnalyticsUnavailableError


class BlogAnalyticsAdapter:
    key = "blog"
    adapter_name = "blog-analytics-unavailable"
    adapter_version = "1"
    source_name = "local-markdown-export"
    configured = False

    def collect(self, receipt, definitions, *, observed_at, collection_index):
        del receipt, definitions, observed_at, collection_index
        raise AnalyticsUnavailableError("local Markdown exports have no analytics source")
