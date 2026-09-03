import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lorchestrateur.ai.providers.http import HTTPResponse
from lorchestrateur.config import Settings
from lorchestrateur.domain.content import EvidenceStatus, SourceType
from lorchestrateur.domain.publication import (
    MediaAsset,
    MediaAssetType,
    PublicationMode,
    PublicationRequest,
    PublicationStatus,
)
from lorchestrateur.domain.workflow import ContentJobState, StateMachine
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.persistence.sqlite import SQLiteContentJobRepository
from lorchestrateur.platforms.x import XContentV1, XPostV1
from lorchestrateur.publishing.adapters.blog import BlogExportPublisher
from lorchestrateur.publishing.adapters.facebook import FacebookPublisher
from lorchestrateur.publishing.adapters.instagram import InstagramPublisher
from lorchestrateur.publishing.adapters.x import XPublisher
from lorchestrateur.publishing.contracts import (
    PreparedItem,
    PublicationAmbiguousOutcomeError,
    PublicationPermanentError,
    PublicationPermissionError,
    PublicationTransientError,
    PublicationValidationError,
    PublishedItem,
    ReconciliationResult,
)
from lorchestrateur.publishing.http import PublicationHTTPClient
from lorchestrateur.publishing.registry import PublishingRegistry
from lorchestrateur.publishing.service import PublicationPolicy, PublicationService
from lorchestrateur.web.composition import compose_web_components
from lorchestrateur.worker import run_once


def approved_components(
    platforms=("blog", "x", "instagram", "facebook"),
    *,
    repository=None,
    settings=None,
):
    repository = repository or InMemoryContentJobRepository()
    settings = settings or Settings(
        app_ai_mode="demo",
        publishing_adapter_mode="demo",
        publishing_dry_run=False,
    )
    components = compose_web_components(settings, repository=repository)
    job = components.service.create_job(
        workspace_id="workspace-test",
        idea="Automatiser les opérations IT répétitives avec gouvernance",
        target_platforms=tuple(platforms),
    )
    components.service.begin_research(job.id)
    components.service.add_source(
        job.id,
        title="Source revue",
        source_type=SourceType.MANUAL,
        relevant_excerpt="Les contrôles déterministes préservent la traçabilité.",
        evidence_status=EvidenceStatus.REVIEWED,
    )
    result = components.executor.run(job.id)
    if result.job.state is not ContentJobState.AWAITING_APPROVAL:
        raise AssertionError("test workflow did not reach approval")
    components.service.approve(job.id, approved_by="Relecteur test")
    return components, job.id


def attach_required_instagram_media(components, job_id):
    previews = components.publication_service.preview_job(job_id)
    instagram = next(item for item in previews if item.platform == "instagram")
    for order in range(1, instagram.media_required + 1):
        components.publication_service.attach_media(
            job_id,
            platform_content_id=instagram.platform_content_id,
            media_type=MediaAssetType.IMAGE,
            source_url=f"https://cdn.example.com/slide-{order}.jpg",
            order=order,
            alt_text=f"Diapositive {order}",
        )


class PublicationDomainAndPersistenceTests(unittest.TestCase):
    def test_approved_only_schedule_validation_and_cancellation(self) -> None:
        components, job_id = approved_components(("x",))
        scheduled_at = datetime.now(UTC) + timedelta(hours=2)
        requests = components.publication_service.create_publications(
            job_id,
            requested_by="Opérateur",
            mode=PublicationMode.SCHEDULED,
            scheduled_at=scheduled_at,
        )
        self.assertEqual(requests[0].status, PublicationStatus.SCHEDULED)
        cancelled = components.publication_service.cancel(requests[0].id, cancelled_by="Opérateur")
        self.assertEqual(cancelled.status, PublicationStatus.CANCELLED)
        with self.assertRaises(PublicationValidationError):
            components.publication_service.cancel(requests[0].id, cancelled_by="Opérateur")

        draft = components.service.create_job(
            workspace_id="test", idea="Job non approuvé", target_platforms=("x",)
        )
        with self.assertRaisesRegex(PublicationValidationError, "approved"):
            components.publication_service.create_publications(
                draft.id,
                requested_by="Opérateur",
                mode=PublicationMode.PUBLISH_NOW,
            )

    def test_sqlite_round_trip_claim_expiry_and_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "publication.sqlite3"
            repository = SQLiteContentJobRepository(path)
            settings = Settings(
                app_ai_mode="demo",
                publishing_adapter_mode="demo",
                publishing_dry_run=False,
            )
            components, job_id = approved_components(
                ("x",), repository=repository, settings=settings
            )
            publication = components.publication_service.create_publications(
                job_id,
                requested_by="Opérateur",
                mode=PublicationMode.PUBLISH_NOW,
            )[0]
            now = datetime.now(UTC)
            first = repository.claim_publication(
                publication.id,
                owner="worker-a",
                now=now,
                lease_expires_at=now + timedelta(seconds=30),
            )
            blocked = repository.claim_publication(
                publication.id,
                owner="worker-b",
                now=now + timedelta(seconds=5),
                lease_expires_at=now + timedelta(seconds=60),
            )
            recovered = repository.claim_publication(
                publication.id,
                owner="worker-b",
                now=now + timedelta(seconds=31),
                lease_expires_at=now + timedelta(seconds=90),
            )
            self.assertEqual(first.claim_owner, "worker-a")
            self.assertIsNone(blocked)
            self.assertEqual(recovered.claim_owner, "worker-b")
            completed = components.publication_service.execute(publication.id, owner="worker-b")

            reopened = SQLiteContentJobRepository(path)
            self.assertEqual(
                reopened.get_publication(publication.id).status,
                PublicationStatus.PUBLISHED,
            )
            self.assertEqual(len(reopened.list_publication_receipts(publication.id)), 1)
            self.assertEqual(completed.status, PublicationStatus.PUBLISHED)

    def test_media_url_security_and_order_uniqueness(self) -> None:
        components, job_id = approved_components(("instagram",))
        preview = components.publication_service.preview_job(job_id)[0]
        for unsafe in (
            "http://cdn.example.com/a.jpg",
            "https://localhost/a.jpg",
            "https://127.0.0.1/a.jpg",
            "https://user:secret@example.com/a.jpg",
        ):
            with self.assertRaises(PublicationValidationError):
                components.publication_service.attach_media(
                    job_id,
                    platform_content_id=preview.platform_content_id,
                    media_type=MediaAssetType.IMAGE,
                    source_url=unsafe,
                    order=1,
                    alt_text=None,
                )
        components.publication_service.attach_media(
            job_id,
            platform_content_id=preview.platform_content_id,
            media_type=MediaAssetType.IMAGE,
            source_url="https://cdn.example.com/a.jpg",
            order=1,
            alt_text="Description",
        )
        with self.assertRaises(ValueError):
            components.publication_service.attach_media(
                job_id,
                platform_content_id=preview.platform_content_id,
                media_type=MediaAssetType.IMAGE,
                source_url="https://cdn.example.com/b.jpg",
                order=1,
                alt_text=None,
            )

    def test_expired_inflight_lease_requires_reconciliation_not_blind_retry(self) -> None:
        components, job_id = approved_components(("x",))
        publication = components.publication_service.create_publications(
            job_id,
            requested_by="Opérateur",
            mode=PublicationMode.PUBLISH_NOW,
        )[0]
        claim_time = datetime.now(UTC)
        claimed = components.repository.claim_publication(
            publication.id,
            owner="crashed-worker",
            now=claim_time,
            lease_expires_at=claim_time + timedelta(seconds=1),
        )
        components.publication_service._ensure_job_publishing(job_id)  # noqa: SLF001
        inflight = claimed.transition(PublicationStatus.PUBLISHING, now=claim_time)
        components.repository.save_publication(inflight)

        recovery_time = claim_time + timedelta(seconds=2)
        recovered = components.publication_service.recover_expired_claims(now=recovery_time)

        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].status, PublicationStatus.NEEDS_RECONCILIATION)
        self.assertIsNone(recovered[0].claim_owner)
        self.assertEqual(
            components.repository.claim_due_publications(
                owner="other-worker",
                now=recovery_time,
                lease_expires_at=recovery_time + timedelta(minutes=1),
                limit=10,
            ),
            (),
        )


class PublicationServiceTests(unittest.TestCase):
    def test_multi_platform_demo_delivery_receipts_and_global_completion(self) -> None:
        components, job_id = approved_components()
        attach_required_instagram_media(components, job_id)
        requests = components.publication_service.create_publications(
            job_id,
            requested_by="Opérateur",
            mode=PublicationMode.PUBLISH_NOW,
        )
        for publication in requests:
            completed = components.publication_service.claim_and_execute(
                publication.id, owner="web-test"
            )
            self.assertEqual(completed.status, PublicationStatus.PUBLISHED)

        self.assertEqual(components.repository.get(job_id).state, ContentJobState.PUBLISHED)
        self.assertEqual(
            sum(len(components.repository.list_publication_receipts(item.id)) for item in requests),
            4,
        )
        self.assertIsNone(
            components.publication_service.claim_and_execute(
                requests[0].id, owner="duplicate-worker"
            )
        )
        self.assertEqual(len(components.repository.list_publication_receipts(requests[0].id)), 1)

    def test_dry_run_never_calls_adapter_and_never_marks_published(self) -> None:
        components, job_id = approved_components(
            ("x",),
            settings=Settings(
                app_ai_mode="demo",
                publishing_adapter_mode="demo",
                publishing_dry_run=True,
            ),
        )
        request = components.publication_service.create_publications(
            job_id,
            requested_by="Opérateur",
            mode=PublicationMode.PUBLISH_NOW,
        )[0]
        completed = components.publication_service.claim_and_execute(
            request.id, owner="dry-run-test"
        )
        self.assertEqual(completed.status, PublicationStatus.DRY_RUN_COMPLETED)
        self.assertEqual(components.repository.list_publication_receipts(request.id), ())
        self.assertEqual(components.repository.get(job_id).state, ContentJobState.APPROVED)

    def test_instagram_missing_media_blocks_entire_batch(self) -> None:
        components, job_id = approved_components(("instagram",))
        preview = components.publication_service.preview_job(job_id)[0]
        self.assertFalse(preview.ready)
        self.assertEqual(preview.media_required, 3)
        with self.assertRaises(PublicationValidationError):
            components.publication_service.create_publications(
                job_id,
                requested_by="Opérateur",
                mode=PublicationMode.PUBLISH_NOW,
            )

    def test_transient_retry_is_bounded_and_ambiguous_result_reconciles(self) -> None:
        components, job_id = approved_components(("x",))
        delegate = components.publishing_registry.get("x")

        class ControlledPublisher:
            key = "x"
            adapter_name = "controlled"
            adapter_version = "1"
            configured = True
            destination_label = "Test"

            def __init__(self):
                self.calls = 0
                self.ambiguous = False

            def prepare(self, content, assets):
                return delegate.prepare(content, assets)

            def publish_item(self, publication, item, *, parent_remote_id):
                del publication, item, parent_remote_id
                self.calls += 1
                if self.ambiguous:
                    raise PublicationAmbiguousOutcomeError("uncertain")
                if self.calls == 1:
                    raise PublicationTransientError("temporary")
                return PublishedItem("remote-ok")

            def reconcile(self, publication, receipts):
                del publication, receipts
                return ReconciliationResult(True, "remote-reconciled")

        publisher = ControlledPublisher()
        service = PublicationService(
            components.repository,
            PublishingRegistry((publisher,)),
            StateMachine(),
            PublicationPolicy(demo_mode=True, dry_run=False, max_retries=1),
            sleeper=lambda _delay: None,
        )
        publication = service.create_publications(
            job_id, requested_by="Opérateur", mode=PublicationMode.PUBLISH_NOW
        )[0]
        completed = service.claim_and_execute(publication.id, owner="worker")
        self.assertEqual(completed.status, PublicationStatus.PUBLISHED)
        self.assertEqual(publisher.calls, 2)
        self.assertEqual(len(components.repository.list_publication_attempts(publication.id)), 2)

        components2, job_id2 = approved_components(("x",))
        publisher.ambiguous = True
        publisher.calls = 0
        service2 = PublicationService(
            components2.repository,
            PublishingRegistry((publisher,)),
            StateMachine(),
            PublicationPolicy(demo_mode=True, dry_run=False),
        )
        uncertain = service2.create_publications(
            job_id2, requested_by="Opérateur", mode=PublicationMode.PUBLISH_NOW
        )[0]
        uncertain = service2.claim_and_execute(uncertain.id, owner="worker")
        self.assertEqual(uncertain.status, PublicationStatus.NEEDS_RECONCILIATION)
        reconciled = service2.reconcile(uncertain.id)
        self.assertEqual(reconciled.status, PublicationStatus.PUBLISHED)

    def test_partial_x_thread_resumes_after_last_persisted_receipt(self) -> None:
        components, job_id = approved_components(("x",))
        content = components.repository.list_platform_contents(job_id)[0]
        thread_payload = XContentV1(
            format="thread",
            opening_hook="Trois contrôles",
            posts=(
                XPostV1(1, "Premier contrôle"),
                XPostV1(2, "Deuxième contrôle"),
                XPostV1(3, "Troisième contrôle"),
            ),
            cta="Votre pratique ?",
            source_ids=content.payload.source_ids,
        )
        components.repository._platform_contents[content.id] = replace(  # noqa: SLF001
            content, payload=thread_payload, format="thread"
        )
        delegate = components.publishing_registry.get("x")

        class PartialThreadPublisher:
            key = "x"
            adapter_name = "partial-thread"
            adapter_version = "1"
            configured = True
            destination_label = "Thread test"

            def __init__(self):
                self.calls = []
                self.failed_once = False

            def prepare(self, record, assets):
                return delegate.prepare(record, assets)

            def publish_item(self, publication, item, *, parent_remote_id):
                del publication
                self.calls.append((item.index, parent_remote_id))
                if item.index == 2 and not self.failed_once:
                    self.failed_once = True
                    raise PublicationPermanentError("permanent test failure")
                return PublishedItem(f"remote-{item.index}")

            def reconcile(self, publication, receipts):
                del publication, receipts
                return ReconciliationResult(False)

        publisher = PartialThreadPublisher()
        service = PublicationService(
            components.repository,
            PublishingRegistry((publisher,)),
            StateMachine(),
            PublicationPolicy(demo_mode=True, dry_run=False),
        )
        publication = service.create_publications(
            job_id, requested_by="Opérateur", mode=PublicationMode.PUBLISH_NOW
        )[0]
        failed = service.claim_and_execute(publication.id, owner="worker-a")
        self.assertEqual(failed.status, PublicationStatus.FAILED)
        first_receipts = components.repository.list_publication_receipts(publication.id)
        self.assertEqual([item.item_index for item in first_receipts], [1])

        retry = failed.transition(PublicationStatus.READY, now=datetime.now(UTC))
        components.repository.save_publication(retry)
        completed = service.claim_and_execute(publication.id, owner="worker-b")
        self.assertEqual(completed.status, PublicationStatus.PUBLISHED)
        self.assertEqual(
            [
                item.item_index
                for item in components.repository.list_publication_receipts(publication.id)
            ],
            [1, 2, 3],
        )
        self.assertEqual(
            publisher.calls, [(1, None), (2, "remote-1"), (2, "remote-1"), (3, "remote-2")]
        )

    def test_scheduler_survives_restart_and_duplicate_worker_execution(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "scheduled.sqlite3"
            settings = Settings(
                app_ai_mode="demo",
                database_url=f"sqlite:///{database.as_posix()}",
                publishing_adapter_mode="demo",
                publishing_dry_run=False,
            )
            repository = SQLiteContentJobRepository(database)
            components, job_id = approved_components(
                ("blog",), repository=repository, settings=settings
            )
            scheduled = components.publication_service.create_publications(
                job_id,
                requested_by="Opérateur",
                mode=PublicationMode.SCHEDULED,
                scheduled_at=datetime.now(UTC) + timedelta(days=1),
            )[0]
            components.repository.save_publication(
                replace(scheduled, scheduled_at=datetime.now(UTC) - timedelta(seconds=1))
            )

            self.assertEqual(run_once(settings, owner="worker-restart"), 1)
            self.assertEqual(run_once(settings, owner="worker-restart"), 0)
            reopened = SQLiteContentJobRepository(database)
            self.assertEqual(reopened.get(job_id).state, ContentJobState.PUBLISHED)
            self.assertEqual(len(reopened.list_publication_receipts(scheduled.id)), 1)


class QueueTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post_json(self, url, *, headers, payload, timeout_seconds):
        self.calls.append((url, dict(headers), dict(payload), timeout_seconds))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class PublishingAdapterTests(unittest.TestCase):
    def test_x_single_thread_payload_and_parent_sequence(self) -> None:
        transport = QueueTransport(
            [
                HTTPResponse(201, b'{"data":{"id":"x-1"}}', {}),
                HTTPResponse(201, b'{"data":{"id":"x-2"}}', {}),
            ]
        )
        publisher = XPublisher(
            enabled=True,
            access_token="secret",
            base_url="https://api.x.com",
            http=PublicationHTTPClient(transport),
        )
        request = _request("x")
        first = publisher.publish_item(
            request, PreparedItem(1, "post", {"text": "Premier"}), parent_remote_id=None
        )
        publisher.publish_item(
            request,
            PreparedItem(2, "post", {"text": "Second"}),
            parent_remote_id=first.remote_id,
        )
        self.assertEqual(transport.calls[0][2], {"text": "Premier"})
        self.assertEqual(transport.calls[1][2]["reply"]["in_reply_to_tweet_id"], "x-1")
        self.assertNotIn("secret", repr(transport.calls[0][2]))

    def test_facebook_payload_receipt_permission_and_retryable_failures(self) -> None:
        transport = QueueTransport([HTTPResponse(403, b'{"error":"secret"}', {})])
        publisher = FacebookPublisher(
            enabled=True,
            page_id="page-1",
            access_token="secret",
            base_url="https://graph.facebook.com/v23.0",
            http=PublicationHTTPClient(transport),
        )
        with self.assertRaises(PublicationPermissionError):
            publisher.publish_item(
                _request("facebook"),
                PreparedItem(1, "page_post", {"message": "Message"}),
                parent_remote_id=None,
            )

        transient = QueueTransport([HTTPResponse(503, b"{}", {})])
        publisher = FacebookPublisher(
            enabled=True,
            page_id="page-1",
            access_token="secret",
            base_url="https://graph.facebook.com/v23.0",
            http=PublicationHTTPClient(transient),
        )
        with self.assertRaises(PublicationTransientError):
            publisher.publish_item(
                _request("facebook"),
                PreparedItem(1, "page_post", {"message": "Message"}),
                parent_remote_id=None,
            )

    def test_instagram_carousel_order_and_live_container_flow(self) -> None:
        components, job_id = approved_components(("instagram",))
        content = components.repository.list_platform_contents(job_id)[0]
        assets = tuple(
            MediaAsset(
                id=f"asset-{order}",
                job_id=job_id,
                platform_content_id=content.id,
                media_type=MediaAssetType.IMAGE,
                source_url=f"https://cdn.example.com/{order}.jpg",
                order=order,
                alt_text=None,
                created_at=datetime.now(UTC),
            )
            for order in (3, 1, 2)
        )
        transport = QueueTransport(
            [
                HTTPResponse(200, b'{"id":"child-1"}', {}),
                HTTPResponse(200, b'{"id":"child-2"}', {}),
                HTTPResponse(200, b'{"id":"child-3"}', {}),
                HTTPResponse(200, b'{"id":"carousel"}', {}),
                HTTPResponse(200, b'{"id":"post-1"}', {}),
            ]
        )
        publisher = InstagramPublisher(
            enabled=True,
            account_id="ig-1",
            access_token="secret",
            base_url="https://graph.facebook.com/v23.0",
            http=PublicationHTTPClient(transport),
        )
        prepared = publisher.prepare(content, assets)
        result = publisher.publish_item(
            _request("instagram"), prepared.items[0], parent_remote_id=None
        )
        self.assertEqual(
            prepared.items[0].payload["media_urls"],
            [
                "https://cdn.example.com/1.jpg",
                "https://cdn.example.com/2.jpg",
                "https://cdn.example.com/3.jpg",
            ],
        )
        self.assertEqual(result.remote_id, "post-1")

    def test_blog_export_is_safe_and_distinguished_from_live_web_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            components, job_id = approved_components(("blog",))
            content = components.repository.list_platform_contents(job_id)[0]
            publisher = BlogExportPublisher(enabled=True, export_directory=directory)
            prepared = publisher.prepare(content, ())
            result = publisher.publish_item(
                _request("blog"), prepared.items[0], parent_remote_id=None
            )
            exported = tuple(Path(directory).glob("*.md"))
            self.assertEqual(len(exported), 1)
            self.assertTrue(result.remote_id.startswith("export:"))
            self.assertIsNone(result.remote_url)
            self.assertIn("Livraison locale", prepared.warnings[0])


def _request(platform):
    now = datetime.now(UTC)
    return PublicationRequest(
        id=f"publication-{platform}",
        job_id="job-1",
        platform_content_id=f"content-{platform}",
        platform=platform,
        requested_by="test",
        mode=PublicationMode.PUBLISH_NOW,
        scheduled_at=None,
        idempotency_key=f"key-{platform}",
        status=PublicationStatus.PUBLISHING,
        dry_run=False,
        claim_owner="worker",
        claimed_at=now,
        lease_expires_at=now + timedelta(minutes=1),
        created_at=now,
        updated_at=now,
    )


if __name__ == "__main__":
    unittest.main()
