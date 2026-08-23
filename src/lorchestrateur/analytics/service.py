"""Governed collection, idempotency, history, freshness, and deterministic summaries."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import sleep
from uuid import uuid4

from lorchestrateur.analytics.contracts import (
    AnalyticsCooldownError,
    AnalyticsError,
    AnalyticsRateLimitError,
    AnalyticsResponseError,
    AnalyticsUnavailableError,
)
from lorchestrateur.analytics.metrics import FAMILY_LABELS, built_in_metric_definitions
from lorchestrateur.analytics.registry import AnalyticsRegistry
from lorchestrateur.domain.analytics import (
    AggregationBehavior,
    AnalyticsCollectionRun,
    AnalyticsRunOutcome,
    MetricDefinition,
    MetricSnapshot,
    MetricUnit,
)
from lorchestrateur.domain.publication import PublicationStatus
from lorchestrateur.persistence.contracts import AnalyticsRepository


@dataclass(frozen=True, slots=True)
class AnalyticsPolicy:
    external_collection_enabled: bool = False
    demo_mode: bool = True
    max_retries: int = 2
    minimum_refresh_seconds: int = 300
    collection_offsets_hours: tuple[int, ...] = (1, 6, 24, 72, 168)
    retention_days: int = 730
    stale_after_seconds: int = 7200

    def __post_init__(self) -> None:
        if not 0 <= self.max_retries <= 5:
            raise ValueError("analytics retries must be between 0 and 5")
        if self.minimum_refresh_seconds < 0 or self.stale_after_seconds < 1:
            raise ValueError("analytics timing policy is invalid")
        if self.retention_days < 1:
            raise ValueError("analytics retention must be positive")
        if not self.collection_offsets_hours or any(
            value < 0 for value in self.collection_offsets_hours
        ):
            raise ValueError("analytics collection offsets must be non-negative")
        if tuple(sorted(set(self.collection_offsets_hours))) != self.collection_offsets_hours:
            raise ValueError("analytics collection offsets must be unique and increasing")


@dataclass(frozen=True, slots=True)
class MetricSummary:
    definition: MetricDefinition
    value: Decimal | None
    previous_value: Decimal | None
    change: Decimal | None
    latest_at: datetime | None
    history: tuple[MetricSnapshot, ...]


@dataclass(frozen=True, slots=True)
class PlatformPerformance:
    platform: str
    metrics: tuple[MetricSummary, ...]
    latest_at: datetime | None
    freshness: str
    collection_status: str
    source_label: str
    receipt_count: int
    next_collection_at: datetime | None


class AnalyticsService:
    def __init__(
        self,
        repository: AnalyticsRepository,
        registry: AnalyticsRegistry,
        policy: AnalyticsPolicy,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = lambda: str(uuid4()),
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self.policy = policy
        self._clock = clock
        self._id_factory = id_factory
        self._sleeper = sleeper
        for definition in built_in_metric_definitions():
            repository.upsert_metric_definition(definition)

    def collect_receipt(
        self,
        receipt_id: str,
        *,
        collection_key: str | None = None,
        bypass_cooldown: bool = False,
    ) -> AnalyticsCollectionRun:
        receipt = self._repository.get_publication_receipt(receipt_id)
        publication = self._repository.get_publication(receipt.publication_id)
        if publication.status is not PublicationStatus.PUBLISHED or publication.dry_run:
            raise AnalyticsUnavailableError("analytics requires a confirmed delivery receipt")
        now = self._clock()
        runs = self._repository.list_analytics_runs(receipt_id=receipt.id)
        manual_request = collection_key is None
        key = collection_key or self._manual_collection_key(receipt.id, now)
        existing = self._repository.get_analytics_run_by_idempotency_key(key)
        if manual_request and not bypass_cooldown and runs:
            latest = max(item.started_at for item in runs)
            if (now - latest).total_seconds() < self.policy.minimum_refresh_seconds:
                raise AnalyticsCooldownError("analytics refresh cooldown is active")
        if existing is not None and existing.outcome is not AnalyticsRunOutcome.RUNNING:
            return existing
        if not manual_request and not bypass_cooldown and existing is None and runs:
            latest = max(item.started_at for item in runs)
            if (now - latest).total_seconds() < self.policy.minimum_refresh_seconds:
                raise AnalyticsCooldownError("analytics refresh cooldown is active")
        adapter = self._registry.get(receipt.platform)
        run = existing or self._repository.add_analytics_run(
            AnalyticsCollectionRun(
                id=self._id_factory(),
                idempotency_key=key,
                platform=receipt.platform,
                publication_receipt_id=receipt.id,
                job_id=publication.job_id,
                started_at=now,
                completed_at=None,
                outcome=AnalyticsRunOutcome.RUNNING,
                adapter_name=adapter.adapter_name,
                adapter_version=adapter.adapter_version,
                error_classification=None,
                metrics_collected_count=0,
            )
        )
        definitions = self._repository.list_metric_definitions(platform=receipt.platform)
        if not definitions:
            return self._complete_error(run, AnalyticsUnavailableError("no metrics supported"), 0)
        if not self.policy.demo_mode and not self.policy.external_collection_enabled:
            return self._complete_error(
                run,
                AnalyticsUnavailableError("external analytics is disabled by policy"),
                0,
            )
        if not self.policy.demo_mode and not adapter.configured:
            return self._complete_error(
                run,
                AnalyticsUnavailableError("analytics adapter is not configured"),
                0,
            )
        collection_index = 1 + sum(
            item.outcome in {AnalyticsRunOutcome.SUCCEEDED, AnalyticsRunOutcome.PARTIAL}
            for item in runs
        )
        for retry_count in range(self.policy.max_retries + 1):
            try:
                result = adapter.collect(
                    receipt,
                    definitions,
                    observed_at=now,
                    collection_index=collection_index,
                )
                snapshots, unavailable = self._persist_result(
                    run,
                    publication.platform_content_id,
                    receipt,
                    definitions,
                    result,
                    adapter.source_name,
                    adapter.adapter_version,
                    now,
                )
                outcome = (
                    AnalyticsRunOutcome.SUCCEEDED
                    if len(snapshots) == len(definitions)
                    else AnalyticsRunOutcome.PARTIAL
                    if snapshots
                    else AnalyticsRunOutcome.UNAVAILABLE
                )
                completed = run.complete(
                    outcome,
                    now=self._clock(),
                    metrics_collected_count=len(snapshots),
                    unavailable_metric_keys=unavailable,
                    retry_count=retry_count,
                )
                self._repository.save_analytics_run(completed)
                return completed
            except AnalyticsError as exc:
                if exc.retryable and retry_count < self.policy.max_retries:
                    self._sleeper(_retry_delay(exc, retry_count))
                    continue
                return self._complete_error(run, exc, retry_count)
        raise AssertionError("bounded analytics retry loop exited unexpectedly")

    def collect_due(self, *, limit: int = 20) -> int:
        now = self._clock()
        collected = 0
        for publication in self._repository.list_publications():
            if publication.status is not PublicationStatus.PUBLISHED or publication.dry_run:
                continue
            for receipt in self._repository.list_publication_receipts(publication.id):
                runs = self._repository.list_analytics_runs(receipt_id=receipt.id)
                completed_windows = sum(
                    item.outcome
                    in {
                        AnalyticsRunOutcome.SUCCEEDED,
                        AnalyticsRunOutcome.PARTIAL,
                        AnalyticsRunOutcome.UNAVAILABLE,
                    }
                    for item in runs
                )
                if completed_windows >= len(self.policy.collection_offsets_hours):
                    continue
                running = next(
                    (
                        item
                        for item in reversed(runs)
                        if item.outcome is AnalyticsRunOutcome.RUNNING
                    ),
                    None,
                )
                if running is not None:
                    self.collect_receipt(
                        receipt.id,
                        collection_key=running.idempotency_key,
                        bypass_cooldown=True,
                    )
                    collected += 1
                    if collected >= limit:
                        return collected
                    continue
                latest_run = runs[-1] if runs else None
                if (
                    latest_run is not None
                    and latest_run.outcome is AnalyticsRunOutcome.FAILED
                    and latest_run.error_classification
                    in {"authentication", "permission", "permanent", "response"}
                ):
                    continue
                recent = max((item.started_at for item in runs), default=None)
                if recent and (now - recent).total_seconds() < self.policy.minimum_refresh_seconds:
                    continue
                offset = self.policy.collection_offsets_hours[completed_windows]
                if now < receipt.published_at + timedelta(hours=offset):
                    continue
                bucket_size = max(1, self.policy.minimum_refresh_seconds)
                bucket = int(now.timestamp() // bucket_size)
                self.collect_receipt(
                    receipt.id,
                    collection_key=(
                        f"{receipt.id}:offset:{completed_windows}:{offset}:{bucket}"
                    ),
                    bypass_cooldown=True,
                )
                collected += 1
                if collected >= limit:
                    return collected
        return collected

    def summarize_job(self, job_id: str) -> tuple[PlatformPerformance, ...]:
        now = self._clock()
        snapshots = self._repository.list_metric_snapshots(job_id=job_id)
        publications = self._repository.list_publications(job_id)
        receipts_by_platform = defaultdict(list)
        for publication in publications:
            for receipt in self._repository.list_publication_receipts(publication.id):
                receipts_by_platform[receipt.platform].append(receipt)
        result = []
        platforms = tuple(
            dict.fromkeys((*receipts_by_platform, *(item.platform for item in snapshots)))
        )
        for platform in platforms:
            platform_snapshots = [item for item in snapshots if item.platform == platform]
            summaries = []
            for definition in self._repository.list_metric_definitions(platform=platform):
                history = tuple(
                    item for item in platform_snapshots if item.metric_key == definition.key
                )
                latest_by_receipt = _latest_by_receipt(history)
                previous_by_receipt = _previous_by_receipt(history)
                latest_value = (
                    sum((item.value for item in latest_by_receipt.values()), Decimal(0))
                    if latest_by_receipt
                    else None
                )
                previous_value = (
                    sum((item.value for item in previous_by_receipt.values()), Decimal(0))
                    if previous_by_receipt and len(previous_by_receipt) == len(latest_by_receipt)
                    else None
                )
                change = (
                    latest_value - previous_value
                    if definition.aggregation is AggregationBehavior.CUMULATIVE
                    and latest_value is not None
                    and previous_value is not None
                    else None
                )
                summaries.append(
                    MetricSummary(
                        definition,
                        latest_value,
                        previous_value,
                        change,
                        max(
                            (item.collected_at for item in latest_by_receipt.values()), default=None
                        ),
                        history,
                    )
                )
            latest_at = max((item.collected_at for item in platform_snapshots), default=None)
            runs = self._repository.list_analytics_runs(job_id=job_id)
            platform_runs = [item for item in runs if item.platform == platform]
            platform_receipts = receipts_by_platform[platform]
            next_collection_at = _next_collection_at(
                platform_receipts,
                platform_runs,
                self.policy.collection_offsets_hours,
            )
            result.append(
                PlatformPerformance(
                    platform=platform,
                    metrics=tuple(summaries),
                    latest_at=latest_at,
                    freshness=_freshness(latest_at, now, self.policy.stale_after_seconds),
                    collection_status=_collection_status(
                        platform_runs, latest_at, next_collection_at
                    ),
                    source_label=(
                        "Données de démonstration"
                        if self.policy.demo_mode
                        else "Données de plateforme"
                    ),
                    receipt_count=len(platform_receipts),
                    next_collection_at=next_collection_at,
                )
            )
        return tuple(result)

    def window_value(
        self,
        receipt_id: str,
        metric_key: str,
        *,
        hours: int,
    ) -> Decimal | None:
        receipt = self._repository.get_publication_receipt(receipt_id)
        cutoff = receipt.published_at + timedelta(hours=hours)
        eligible = [
            item
            for item in self._repository.list_metric_snapshots(
                receipt_id=receipt_id, metric_key=metric_key
            )
            if item.observed_at <= cutoff
        ]
        return max(eligible, key=lambda item: item.observed_at).value if eligible else None

    def prune_retention(self) -> int:
        return self._repository.prune_metric_snapshots(
            collected_before=self._clock() - timedelta(days=self.policy.retention_days)
        )

    def _manual_collection_key(self, receipt_id: str, now: datetime) -> str:
        bucket = int(now.timestamp() // max(1, self.policy.minimum_refresh_seconds))
        return f"{receipt_id}:manual:{bucket}"

    def _persist_result(
        self,
        run,
        platform_content_id,
        receipt,
        definitions,
        result,
        source,
        source_version,
        collected_at,
    ):
        definition_by_key = {item.key: item for item in definitions}
        unavailable = set(result.unavailable_metric_keys)
        seen = set()
        pending = []
        for observation in result.observations:
            if observation.metric_key in seen or observation.metric_key not in definition_by_key:
                raise AnalyticsResponseError(
                    "analytics adapter returned an invalid metric identity"
                )
            seen.add(observation.metric_key)
            definition = definition_by_key[observation.metric_key]
            try:
                value = Decimal(observation.value)
            except Exception as exc:
                raise AnalyticsResponseError(
                    "analytics adapter returned an invalid metric value"
                ) from exc
            if value < 0 or not value.is_finite():
                raise AnalyticsResponseError("analytics adapter returned an invalid metric value")
            if definition.unit is MetricUnit.COUNT and value != value.to_integral_value():
                raise AnalyticsResponseError("count metric must use an integer value")
            pending.append(
                MetricSnapshot(
                    id=self._id_factory(),
                    collection_run_id=run.id,
                    publication_receipt_id=receipt.id,
                    job_id=run.job_id,
                    platform_content_id=platform_content_id,
                    platform=receipt.platform,
                    metric_key=observation.metric_key,
                    value=value,
                    observed_at=observation.observed_at,
                    period_start=observation.period_start,
                    period_end=observation.period_end,
                    source=source,
                    source_version=source_version,
                    collected_at=collected_at,
                    metadata={"demo": self.policy.demo_mode},
                )
            )
        unavailable.update(set(definition_by_key) - seen)
        if seen & unavailable:
            raise AnalyticsResponseError(
                "metric cannot be observed and unavailable in one run"
            )
        snapshots = tuple(self._repository.add_metric_snapshot(item) for item in pending)
        return tuple(snapshots), tuple(sorted(unavailable))

    def _complete_error(
        self, run: AnalyticsCollectionRun, error: AnalyticsError, retry_count: int
    ) -> AnalyticsCollectionRun:
        outcome = (
            AnalyticsRunOutcome.RATE_LIMITED
            if isinstance(error, AnalyticsRateLimitError)
            else AnalyticsRunOutcome.UNAVAILABLE
            if isinstance(error, AnalyticsUnavailableError)
            else AnalyticsRunOutcome.FAILED
        )
        completed = run.complete(
            outcome,
            now=self._clock(),
            metrics_collected_count=0,
            error_classification=error.classification,
            retry_count=retry_count,
        )
        self._repository.save_analytics_run(completed)
        return completed


def _latest_by_receipt(history: tuple[MetricSnapshot, ...]) -> dict[str, MetricSnapshot]:
    latest = {}
    for item in history:
        current = latest.get(item.publication_receipt_id)
        if current is None or (item.observed_at, item.collected_at) > (
            current.observed_at,
            current.collected_at,
        ):
            latest[item.publication_receipt_id] = item
    return latest


def _previous_by_receipt(history: tuple[MetricSnapshot, ...]) -> dict[str, MetricSnapshot]:
    grouped = defaultdict(list)
    for item in history:
        grouped[item.publication_receipt_id].append(item)
    return {
        receipt_id: sorted(items, key=lambda item: (item.observed_at, item.collected_at))[-2]
        for receipt_id, items in grouped.items()
        if len(items) >= 2
    }


def _freshness(latest_at: datetime | None, now: datetime, stale_after: int) -> str:
    if latest_at is None:
        return "Jamais synchronisé"
    age = max(0, int((now - latest_at).total_seconds()))
    if age > stale_after:
        return "Données anciennes"
    if age < 60:
        return "À l’instant"
    if age < 3600:
        return f"Il y a {age // 60} min"
    return f"Il y a {age // 3600} h"


def _collection_status(
    runs: list[AnalyticsCollectionRun],
    latest_at: datetime | None,
    next_collection_at: datetime | None,
) -> str:
    if not runs:
        return "Synchronisation prévue" if next_collection_at else "Jamais collecté"
    latest = max(runs, key=lambda item: item.started_at)
    labels = {
        AnalyticsRunOutcome.RUNNING: "Synchronisation en cours",
        AnalyticsRunOutcome.SUCCEEDED: "À jour",
        AnalyticsRunOutcome.PARTIAL: "Données partielles",
        AnalyticsRunOutcome.FAILED: "Erreur de collecte",
        AnalyticsRunOutcome.RATE_LIMITED: "Limite API",
        AnalyticsRunOutcome.UNAVAILABLE: "Métriques indisponibles",
    }
    return labels[latest.outcome] if latest_at is not None or runs else "Jamais collecté"


def _next_collection_at(receipts, runs, offsets: tuple[int, ...]) -> datetime | None:
    runs_by_receipt = defaultdict(list)
    for run in runs:
        runs_by_receipt[run.publication_receipt_id].append(run)
    candidates = []
    for receipt in receipts:
        receipt_runs = runs_by_receipt[receipt.id]
        completed_windows = sum(
            item.outcome
            in {
                AnalyticsRunOutcome.SUCCEEDED,
                AnalyticsRunOutcome.PARTIAL,
                AnalyticsRunOutcome.UNAVAILABLE,
            }
            for item in receipt_runs
        )
        if completed_windows >= len(offsets):
            continue
        latest = max(receipt_runs, key=lambda item: item.started_at, default=None)
        if latest is not None and (
            latest.outcome is AnalyticsRunOutcome.RUNNING
            or (
                latest.outcome is AnalyticsRunOutcome.FAILED
                and latest.error_classification
                in {"authentication", "permission", "permanent", "response"}
            )
        ):
            continue
        candidates.append(receipt.published_at + timedelta(hours=offsets[completed_windows]))
    return min(candidates, default=None)


def _retry_delay(error: AnalyticsError, retry_count: int) -> float:
    if isinstance(error, AnalyticsRateLimitError) and error.retry_after_seconds is not None:
        return min(5.0, max(0.0, error.retry_after_seconds))
    return min(2.0, 0.25 * (2**retry_count))


def family_label(summary: MetricSummary) -> str:
    return FAMILY_LABELS[summary.definition.family]
