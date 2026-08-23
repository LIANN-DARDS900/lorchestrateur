import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from lorchestrateur.ai.providers.http import HTTPResponse
from lorchestrateur.analytics.adapters.blog import BlogAnalyticsAdapter
from lorchestrateur.analytics.adapters.demo import DemoAnalyticsAdapter
from lorchestrateur.analytics.adapters.meta import (
    FacebookAnalyticsAdapter,
    InstagramAnalyticsAdapter,
)
from lorchestrateur.analytics.adapters.x import XAnalyticsAdapter
from lorchestrateur.analytics.contracts import (
    AnalyticsAuthenticationError,
    AnalyticsPermissionError,
    AnalyticsRateLimitError,
    AnalyticsResponseError,
    AnalyticsResult,
    AnalyticsTransientError,
    AnalyticsUnavailableError,
    MetricObservation,
)
from lorchestrateur.analytics.http import AnalyticsHTTPClient
from lorchestrateur.analytics.metrics import built_in_metric_definitions
from lorchestrateur.analytics.registry import AnalyticsRegistry
from lorchestrateur.analytics.service import AnalyticsPolicy, AnalyticsService
from lorchestrateur.analytics_worker import run_once as run_analytics_once
from lorchestrateur.config import Settings
from lorchestrateur.domain.analytics import (
    AggregationBehavior,
    AnalyticsCollectionRun,
    AnalyticsRunOutcome,
    MetricFamily,
    MetricSnapshot,
    MetricUnit,
)
from lorchestrateur.domain.content import EvidenceStatus, SourceType
from lorchestrateur.domain.publication import MediaAssetType, PublicationMode
from lorchestrateur.domain.workflow import ContentJobState
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.persistence.sqlite import SQLiteContentJobRepository
from lorchestrateur.web.composition import compose_web_components


class MutableClock:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


class StaticGETTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, headers, timeout_seconds):
        self.calls.append((url, dict(headers), timeout_seconds))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def published_demo(platforms=("x",), *, repository=None, settings=None):
    repository = repository or InMemoryContentJobRepository()
    settings = settings or Settings(
        app_ai_mode="demo",
        publishing_adapter_mode="demo",
        publishing_dry_run=False,
        analytics_adapter_mode="demo",
        analytics_min_refresh_seconds=0,
        analytics_collection_offsets_hours=(0, 1),
    )
    components = compose_web_components(settings, repository=repository)
    job = components.service.create_job(
        workspace_id="analytics-tests",
        idea="Mesurer une orchestration gouvernée après publication",
        target_platforms=tuple(platforms),
    )
    components.service.begin_research(job.id)
    components.service.add_source(
        job.id,
        title="Source revue",
        source_type=SourceType.MANUAL,
        relevant_excerpt="Les mesures restent liées à une publication connue.",
        evidence_status=EvidenceStatus.REVIEWED,
    )
    result = components.executor.run(job.id)
    if result.job.state is not ContentJobState.AWAITING_APPROVAL:
        raise AssertionError("analytics test workflow did not reach approval")
    components.service.approve(job.id, approved_by="Analyste test")
    for preview in components.publication_service.preview_job(job.id):
        if preview.platform != "instagram":
            continue
        for order in range(1, preview.media_required + 1):
            components.publication_service.attach_media(
                job.id,
                platform_content_id=preview.platform_content_id,
                media_type=MediaAssetType.IMAGE,
                source_url=f"https://cdn.example.com/analytics-{order}.jpg",
                order=order,
                alt_text=f"Visuel {order}",
            )
    publications = components.publication_service.create_publications(
        job.id,
        requested_by="Analyste test",
        mode=PublicationMode.PUBLISH_NOW,
    )
    for publication in publications:
        components.publication_service.claim_and_execute(publication.id, owner="analytics-test")
    if repository.get(job.id).state is not ContentJobState.PUBLISHED:
        raise AssertionError("analytics test workflow did not publish")
    receipts = tuple(
        receipt
        for publication in repository.list_publications(job.id)
        for receipt in repository.list_publication_receipts(publication.id)
    )
    return components, job.id, receipts


class AnalyticsDomainTests(unittest.TestCase):
    def test_configuration_defaults_are_safe_and_demo_first(self) -> None:
        settings = Settings(
            x_analytics_bearer_token="secret-token",
            meta_analytics_access_token="secret-token",
        )
        self.assertFalse(settings.analytics_enabled)
        self.assertEqual(settings.analytics_adapter_mode, "demo")
        self.assertFalse(settings.x_analytics_enabled)
        self.assertFalse(settings.meta_analytics_enabled)
        self.assertNotIn("secret-token", repr(settings))

    def test_definitions_preserve_families_units_and_cumulative_semantics(self) -> None:
        definitions = built_in_metric_definitions()
        reach = next(item for item in definitions if item.key == "instagram.reach")
        replies = next(item for item in definitions if item.key == "x.replies")
        self.assertEqual(reach.family, MetricFamily.EXPOSURE)
        self.assertEqual(replies.family, MetricFamily.CONVERSATION)
        self.assertEqual(reach.unit, MetricUnit.COUNT)
        self.assertEqual(reach.aggregation, AggregationBehavior.CUMULATIVE)
        self.assertNotIn("blog.views", {item.key for item in definitions})

    def test_snapshot_zero_is_observed_while_missing_is_absent(self) -> None:
        now = datetime.now(UTC)
        snapshot = MetricSnapshot(
            id="snapshot-zero",
            collection_run_id="run-zero",
            publication_receipt_id="receipt-zero",
            job_id="job-zero",
            platform_content_id="content-zero",
            platform="x",
            metric_key="x.likes",
            value=0,
            observed_at=now,
            period_start=None,
            period_end=None,
            source="manual.test",
            source_version="1",
            collected_at=now,
        )
        self.assertEqual(snapshot.value, Decimal(0))
        repository = InMemoryContentJobRepository()
        repository.add_metric_snapshot(snapshot)
        self.assertEqual(repository.list_metric_snapshots(metric_key="x.likes")[0].value, 0)
        self.assertEqual(repository.list_metric_snapshots(metric_key="x.impressions"), ())

    def test_collection_run_has_sanitized_terminal_metadata(self) -> None:
        now = datetime.now(UTC)
        running = AnalyticsCollectionRun(
            id="run",
            idempotency_key="receipt:manual:1",
            platform="x",
            publication_receipt_id="receipt",
            job_id="job",
            started_at=now,
            completed_at=None,
            outcome=AnalyticsRunOutcome.RUNNING,
            adapter_name="test",
            adapter_version="1",
            error_classification=None,
            metrics_collected_count=0,
        )
        completed = running.complete(
            AnalyticsRunOutcome.PARTIAL,
            now=now + timedelta(seconds=1),
            metrics_collected_count=2,
            unavailable_metric_keys=("x.bookmarks",),
            retry_count=1,
        )
        self.assertEqual(completed.metrics_collected_count, 2)
        self.assertEqual(completed.unavailable_metric_keys, ("x.bookmarks",))


class AnalyticsPersistenceAndServiceTests(unittest.TestCase):
    def test_sqlite_history_restart_idempotency_and_cumulative_latest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "analytics.sqlite3"
            repository = SQLiteContentJobRepository(path)
            settings = Settings(
                app_ai_mode="demo",
                publishing_adapter_mode="demo",
                publishing_dry_run=False,
                analytics_adapter_mode="demo",
                analytics_min_refresh_seconds=0,
            )
            _components, job_id, receipts = published_demo(
                ("x",), repository=repository, settings=settings
            )
            receipt = receipts[0]
            clock = MutableClock(receipt.published_at + timedelta(hours=1))
            service = AnalyticsService(
                repository,
                AnalyticsRegistry((DemoAnalyticsAdapter("x"),)),
                AnalyticsPolicy(demo_mode=True, minimum_refresh_seconds=0),
                clock=clock,
            )
            first = service.collect_receipt(
                receipt.id, collection_key="first", bypass_cooldown=True
            )
            first_snapshots = repository.list_metric_snapshots(receipt_id=receipt.id)
            duplicate = service.collect_receipt(
                receipt.id, collection_key="first", bypass_cooldown=True
            )
            self.assertEqual(duplicate.id, first.id)
            self.assertEqual(
                repository.list_metric_snapshots(receipt_id=receipt.id), first_snapshots
            )

            clock.value += timedelta(hours=5)
            service.collect_receipt(receipt.id, collection_key="second", bypass_cooldown=True)
            reopened = SQLiteContentJobRepository(path)
            history = reopened.list_metric_snapshots(
                receipt_id=receipt.id, metric_key="x.impressions"
            )
            self.assertEqual(len(history), 2)
            reopened_service = AnalyticsService(
                reopened,
                AnalyticsRegistry((DemoAnalyticsAdapter("x"),)),
                AnalyticsPolicy(demo_mode=True, minimum_refresh_seconds=0),
                clock=clock,
            )
            metric = next(
                item
                for item in reopened_service.summarize_job(job_id)[0].metrics
                if item.definition.key == "x.impressions"
            )
            self.assertEqual(metric.value, history[-1].value)
            self.assertNotEqual(metric.value, history[0].value + history[-1].value)
            self.assertEqual(metric.change, history[-1].value - history[0].value)
            self.assertEqual(
                reopened_service.window_value(receipt.id, "x.impressions", hours=24),
                history[-1].value,
            )

            connection = sqlite3.connect(path)
            try:
                indexes = {
                    row[1]
                    for row in connection.execute("PRAGMA index_list('metric_snapshots')")
                }
            finally:
                connection.close()
            self.assertIn("idx_metric_snapshots_receipt_metric", indexes)

    def test_failure_and_rate_limit_preserve_previous_history_with_bounded_retry(self) -> None:
        components, job_id, receipts = published_demo(("x",))
        receipt = receipts[0]

        class ControlledAdapter:
            key = "x"
            adapter_name = "controlled"
            adapter_version = "1"
            source_name = "controlled.test"
            configured = True

            def __init__(self):
                self.calls = 0

            def collect(self, receipt, definitions, *, observed_at, collection_index):
                del receipt, collection_index
                self.calls += 1
                if self.calls == 1:
                    return AnalyticsResult(
                        (MetricObservation(definitions[0].key, 10, observed_at),),
                        tuple(item.key for item in definitions[1:]),
                    )
                raise AnalyticsRateLimitError("limited", retry_after_seconds=0)

        adapter = ControlledAdapter()
        clock = MutableClock(receipt.published_at + timedelta(hours=1))
        service = AnalyticsService(
            components.repository,
            AnalyticsRegistry((adapter,)),
            AnalyticsPolicy(demo_mode=True, max_retries=1, minimum_refresh_seconds=0),
            clock=clock,
            sleeper=lambda _delay: None,
        )
        service.collect_receipt(receipt.id, collection_key="success", bypass_cooldown=True)
        baseline = components.repository.list_metric_snapshots(receipt_id=receipt.id)
        clock.value += timedelta(hours=1)
        limited = service.collect_receipt(
            receipt.id, collection_key="limited", bypass_cooldown=True
        )
        self.assertEqual(limited.outcome, AnalyticsRunOutcome.RATE_LIMITED)
        self.assertEqual(limited.retry_count, 1)
        self.assertEqual(adapter.calls, 3)
        self.assertEqual(
            components.repository.list_metric_snapshots(receipt_id=receipt.id), baseline
        )
        self.assertEqual(service.summarize_job(job_id)[0].metrics[0].value, Decimal(10))

    def test_worker_collects_due_after_restart_without_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "worker.sqlite3"
            settings = Settings(
                database_url=f"sqlite:///{path}",
                app_ai_mode="demo",
                publishing_adapter_mode="demo",
                publishing_dry_run=False,
                analytics_adapter_mode="demo",
                analytics_collection_offsets_hours=(0,),
                analytics_min_refresh_seconds=1,
            )
            repository = SQLiteContentJobRepository(path)
            _components, _job_id, receipts = published_demo(
                ("x",), repository=repository, settings=settings
            )
            self.assertEqual(run_analytics_once(settings), 1)
            restarted = SQLiteContentJobRepository(path)
            count = len(restarted.list_metric_snapshots(receipt_id=receipts[0].id))
            self.assertGreater(count, 0)
            self.assertEqual(run_analytics_once(settings), 0)
            self.assertEqual(
                len(SQLiteContentJobRepository(path).list_metric_snapshots(receipt_id=receipts[0].id)),
                count,
            )

    def test_worker_resumes_running_collection_after_process_restart(self) -> None:
        components, _job_id, receipts = published_demo(("x",))
        receipt = receipts[0]
        clock = MutableClock(receipt.published_at + timedelta(hours=2))
        running = AnalyticsCollectionRun(
            id="interrupted-run",
            idempotency_key="interrupted-worker-window",
            platform="x",
            publication_receipt_id=receipt.id,
            job_id=components.repository.get_publication(receipt.publication_id).job_id,
            started_at=receipt.published_at + timedelta(hours=1),
            completed_at=None,
            outcome=AnalyticsRunOutcome.RUNNING,
            adapter_name="deterministic-demo-analytics",
            adapter_version="1",
            error_classification=None,
            metrics_collected_count=0,
        )
        components.repository.add_analytics_run(running)
        service = AnalyticsService(
            components.repository,
            AnalyticsRegistry((DemoAnalyticsAdapter("x"),)),
            AnalyticsPolicy(
                demo_mode=True,
                minimum_refresh_seconds=0,
                collection_offsets_hours=(0,),
            ),
            clock=clock,
        )
        self.assertEqual(service.collect_due(), 1)
        resumed = components.repository.get_analytics_run_by_idempotency_key(
            running.idempotency_key
        )
        self.assertEqual(resumed.id, running.id)
        self.assertEqual(resumed.outcome, AnalyticsRunOutcome.SUCCEEDED)
        self.assertEqual(len(components.repository.list_analytics_runs(receipt_id=receipt.id)), 1)

    def test_worker_advances_past_terminal_unavailable_window(self) -> None:
        components, _job_id, receipts = published_demo(("blog",))
        receipt = receipts[0]
        clock = MutableClock(receipt.published_at + timedelta(hours=1))
        service = AnalyticsService(
            components.repository,
            AnalyticsRegistry((BlogAnalyticsAdapter(),)),
            AnalyticsPolicy(
                demo_mode=True,
                minimum_refresh_seconds=0,
                collection_offsets_hours=(0,),
            ),
            clock=clock,
        )
        self.assertEqual(service.collect_due(), 1)
        run_count = len(components.repository.list_analytics_runs(receipt_id=receipt.id))
        clock.value += timedelta(hours=1)
        self.assertEqual(service.collect_due(), 0)
        self.assertEqual(
            len(components.repository.list_analytics_runs(receipt_id=receipt.id)), run_count
        )

    def test_worker_does_not_repeat_nonretryable_permission_failure(self) -> None:
        components, _job_id, receipts = published_demo(("x",))
        receipt = receipts[0]
        failure_at = receipt.published_at + timedelta(hours=1)
        components.repository.add_analytics_run(
            AnalyticsCollectionRun(
                id="permission-failure",
                idempotency_key="permission-failure-window",
                platform="x",
                publication_receipt_id=receipt.id,
                job_id=components.repository.get_publication(receipt.publication_id).job_id,
                started_at=failure_at,
                completed_at=failure_at,
                outcome=AnalyticsRunOutcome.FAILED,
                adapter_name="x-public-metrics-v2",
                adapter_version="1",
                error_classification="permission",
                metrics_collected_count=0,
            )
        )
        service = AnalyticsService(
            components.repository,
            AnalyticsRegistry((DemoAnalyticsAdapter("x"),)),
            AnalyticsPolicy(
                demo_mode=True,
                minimum_refresh_seconds=0,
                collection_offsets_hours=(0,),
            ),
            clock=MutableClock(failure_at + timedelta(hours=1)),
        )
        self.assertEqual(service.collect_due(), 0)
        self.assertEqual(components.repository.list_metric_snapshots(receipt_id=receipt.id), ())


class AnalyticsAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.definitions = built_in_metric_definitions()
        now = datetime.now(UTC)
        self.receipt = type(
            "Receipt",
            (),
            {"remote_id": "remote-1", "platform": "x", "published_at": now},
        )()
        self.now = now

    def test_demo_adapter_is_deterministic_and_no_network(self) -> None:
        definitions = tuple(item for item in self.definitions if item.platform == "x")
        adapter = DemoAnalyticsAdapter("x")
        first = adapter.collect(
            self.receipt, definitions, observed_at=self.now, collection_index=1
        )
        again = adapter.collect(
            self.receipt, definitions, observed_at=self.now, collection_index=1
        )
        later = adapter.collect(
            self.receipt, definitions, observed_at=self.now, collection_index=2
        )
        self.assertEqual(first, again)
        self.assertGreater(later.observations[0].value, first.observations[0].value)
        self.assertEqual(adapter.source_name, "demo.analytics.v1")

    def test_x_success_missing_auth_rate_limit_and_malformed_response(self) -> None:
        definitions = tuple(item for item in self.definitions if item.platform == "x")
        body = (
            b'{"data":{"public_metrics":{"impression_count":100,"like_count":9,'
            b'"reply_count":2,"retweet_count":3,"quote_count":1}}}'
        )
        transport = StaticGETTransport(HTTPResponse(200, body, {}))
        adapter = XAnalyticsAdapter(
            enabled=True,
            bearer_token="secret",
            base_url="https://api.x.com",
            timeout_seconds=5,
            http=AnalyticsHTTPClient(transport),
        )
        result = adapter.collect(
            self.receipt, definitions, observed_at=self.now, collection_index=1
        )
        self.assertEqual(len(result.observations), 5)
        self.assertEqual(result.unavailable_metric_keys, ("x.bookmarks",))
        self.assertNotIn("secret", transport.calls[0][0])

        with self.assertRaises(AnalyticsAuthenticationError):
            AnalyticsHTTPClient(StaticGETTransport(HTTPResponse(401, b"{}", {}))).get_json(
                "x", "https://api.x.com/test", headers={}, timeout_seconds=1
            )
        with self.assertRaises(AnalyticsPermissionError):
            AnalyticsHTTPClient(StaticGETTransport(HTTPResponse(403, b"{}", {}))).get_json(
                "x", "https://api.x.com/test", headers={}, timeout_seconds=1
            )
        with self.assertRaises(AnalyticsRateLimitError):
            AnalyticsHTTPClient(
                StaticGETTransport(HTTPResponse(429, b"{}", {"retry-after": "2"}))
            ).get_json("x", "https://api.x.com/test", headers={}, timeout_seconds=1)
        malformed = XAnalyticsAdapter(
            enabled=True,
            bearer_token="secret",
            base_url="https://api.x.com",
            timeout_seconds=5,
            http=AnalyticsHTTPClient(StaticGETTransport(HTTPResponse(200, b"not-json", {}))),
        )
        with self.assertRaises(AnalyticsResponseError):
            malformed.collect(
                self.receipt, definitions, observed_at=self.now, collection_index=1
            )

    def test_meta_facebook_and_instagram_preserve_missing_metrics(self) -> None:
        facebook_definitions = tuple(
            item for item in self.definitions if item.platform == "facebook"
        )
        facebook = FacebookAnalyticsAdapter(
            enabled=True,
            access_token="secret",
            base_url="https://graph.facebook.com/v23.0",
            timeout_seconds=5,
            http=AnalyticsHTTPClient(
                StaticGETTransport(
                    HTTPResponse(
                        200,
                        b'{"reactions":{"summary":{"total_count":7}},'
                        b'"comments":{"summary":{"total_count":0}},"shares":{"count":2}}',
                        {},
                    )
                )
            ),
        )
        fb_result = facebook.collect(
            self.receipt,
            facebook_definitions,
            observed_at=self.now,
            collection_index=1,
        )
        self.assertEqual([item.value for item in fb_result.observations], [7, 0, 2])

        instagram_definitions = tuple(
            item for item in self.definitions if item.platform == "instagram"
        )
        instagram = InstagramAnalyticsAdapter(
            enabled=True,
            access_token="secret",
            base_url="https://graph.facebook.com/v23.0",
            timeout_seconds=5,
            http=AnalyticsHTTPClient(
                StaticGETTransport(
                    HTTPResponse(
                        200,
                        b'{"data":[{"name":"reach","period":"lifetime",'
                        b'"values":[{"value":42}]},{"name":"likes",'
                        b'"total_value":{"value":0}}]}',
                        {},
                    )
                )
            ),
        )
        ig_result = instagram.collect(
            self.receipt,
            instagram_definitions,
            observed_at=self.now,
            collection_index=1,
        )
        self.assertEqual([item.value for item in ig_result.observations], [42, 0])
        self.assertIn("instagram.views", ig_result.unavailable_metric_keys)
        self.assertNotIn("instagram.likes", ig_result.unavailable_metric_keys)

        with self.assertRaises(AnalyticsPermissionError):
            FacebookAnalyticsAdapter(
                enabled=True,
                access_token="secret",
                base_url="https://graph.facebook.com/v23.0",
                timeout_seconds=5,
                http=AnalyticsHTTPClient(
                    StaticGETTransport(HTTPResponse(403, b"{}", {}))
                ),
            ).collect(
                self.receipt,
                facebook_definitions,
                observed_at=self.now,
                collection_index=1,
            )

        malformed = InstagramAnalyticsAdapter(
            enabled=True,
            access_token="secret",
            base_url="https://graph.facebook.com/v23.0",
            timeout_seconds=5,
            http=AnalyticsHTTPClient(
                StaticGETTransport(HTTPResponse(200, b'{"data":"invalid"}', {}))
            ),
        )
        with self.assertRaises(AnalyticsResponseError):
            malformed.collect(
                self.receipt,
                instagram_definitions,
                observed_at=self.now,
                collection_index=1,
            )

    def test_blog_export_analytics_is_explicitly_unavailable(self) -> None:
        with self.assertRaises(AnalyticsUnavailableError):
            BlogAnalyticsAdapter().collect(
                self.receipt, (), observed_at=self.now, collection_index=1
            )

    def test_transient_transport_failure_is_classified(self) -> None:
        client = AnalyticsHTTPClient(StaticGETTransport(AnalyticsTransientError("temporary")))
        with self.assertRaises(AnalyticsTransientError):
            client.get_json("x", "https://api.x.com/test", headers={}, timeout_seconds=1)


if __name__ == "__main__":
    unittest.main()
