"""Deterministic, no-network demo analytics."""

from __future__ import annotations

import hashlib
from decimal import Decimal

from lorchestrateur.analytics.contracts import AnalyticsResult, MetricObservation
from lorchestrateur.domain.analytics import MetricDefinition, MetricFamily
from lorchestrateur.domain.publication import PublicationReceipt


class DemoAnalyticsAdapter:
    adapter_name = "deterministic-demo-analytics"
    adapter_version = "1"
    source_name = "demo.analytics.v1"
    configured = True

    def __init__(self, platform: str) -> None:
        self.key = platform

    def collect(
        self,
        receipt: PublicationReceipt,
        definitions: tuple[MetricDefinition, ...],
        *,
        observed_at,
        collection_index: int,
    ) -> AnalyticsResult:
        observations = []
        for definition in definitions:
            digest = hashlib.sha256(
                f"{receipt.remote_id}:{definition.key}".encode()
            ).digest()
            seed = int.from_bytes(digest[:4], "big")
            if "zero" in receipt.remote_id.casefold():
                value = 0
            else:
                base = (seed % 180) + 12
                if definition.family is MetricFamily.EXPOSURE:
                    base *= 24
                growth = ((seed % 23) + 3) * max(0, collection_index - 1)
                value = base + growth
            observations.append(
                MetricObservation(
                    metric_key=definition.key,
                    value=Decimal(value),
                    observed_at=observed_at,
                )
            )
        return AnalyticsResult(tuple(observations))
