"""Governed publication authorization, scheduling, execution, and reconciliation."""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import sleep
from urllib.parse import urlparse
from uuid import uuid4

from lorchestrateur.domain.platform_content import PlatformValidationStatus
from lorchestrateur.domain.publication import (
    MediaAsset,
    MediaAssetType,
    PublicationAttempt,
    PublicationAttemptOutcome,
    PublicationMode,
    PublicationReceipt,
    PublicationRequest,
    PublicationStatus,
)
from lorchestrateur.domain.workflow import ContentJobState, StateMachine
from lorchestrateur.persistence.contracts import PublicationRepository
from lorchestrateur.publishing.contracts import (
    PreparedPublication,
    PublicationError,
    PublicationRateLimitError,
    PublicationUnavailableError,
    PublicationValidationError,
    ReconciliationResult,
)
from lorchestrateur.publishing.registry import PublishingRegistry


@dataclass(frozen=True, slots=True)
class PublicationPolicy:
    external_delivery_enabled: bool = False
    dry_run: bool = True
    demo_mode: bool = True
    minimum_quality_score: int = 80
    max_retries: int = 2
    lease_seconds: int = 120


@dataclass(frozen=True, slots=True)
class PublicationPreview:
    platform: str
    platform_content_id: str
    revision: int
    ready: bool
    destination: str
    dry_run: bool
    quality_score: int | None
    warnings: tuple[str, ...]
    media_required: int
    media_attached: int
    prepared: PreparedPublication | None


class PublicationService:
    def __init__(
        self,
        repository: PublicationRepository,
        registry: PublishingRegistry,
        state_machine: StateMachine,
        policy: PublicationPolicy,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_factory: Callable[[], str] = lambda: str(uuid4()),
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        self._repository = repository
        self._registry = registry
        self._state_machine = state_machine
        self.policy = policy
        self._clock = clock
        self._id_factory = id_factory
        self._sleeper = sleeper

    def preview_job(self, job_id: str) -> tuple[PublicationPreview, ...]:
        job = self._repository.get(job_id)
        latest = self._latest_contents(job_id)
        previews = []
        for platform in job.target_platforms:
            content = latest.get(platform)
            if content is None:
                previews.append(
                    PublicationPreview(
                        platform,
                        "",
                        0,
                        False,
                        "Indisponible",
                        self.policy.dry_run,
                        None,
                        ("Adaptation requise manquante.",),
                        0,
                        0,
                        None,
                    )
                )
                continue
            publisher = self._registry.get(platform)
            assets = self._repository.list_media_assets(content.id)
            warnings: list[str] = []
            prepared = None
            ready = True
            try:
                self._validate_content(content)
                prepared = publisher.prepare(content, assets)
                warnings.extend(prepared.warnings)
                if (
                    not self.policy.dry_run
                    and not self.policy.demo_mode
                    and not publisher.configured
                ):
                    raise PublicationUnavailableError(f"{platform} publishing is not configured")
                if (
                    not self.policy.dry_run
                    and not self.policy.demo_mode
                    and not self.policy.external_delivery_enabled
                ):
                    raise PublicationUnavailableError(
                        "live publishing is disabled by application policy"
                    )
            except PublicationError as exc:
                ready = False
                warnings.append(_safe_error_message(exc))
            media_required = _required_media_count(content)
            previews.append(
                PublicationPreview(
                    platform=platform,
                    platform_content_id=content.id,
                    revision=content.revision,
                    ready=ready and prepared is not None,
                    destination=prepared.destination_label
                    if prepared
                    else publisher.destination_label,
                    dry_run=self.policy.dry_run,
                    quality_score=content.quality_score,
                    warnings=tuple(warnings),
                    media_required=media_required,
                    media_attached=len(assets),
                    prepared=prepared,
                )
            )
        return tuple(previews)

    def create_publications(
        self,
        job_id: str,
        *,
        requested_by: str,
        mode: PublicationMode,
        scheduled_at: datetime | None = None,
    ) -> tuple[PublicationRequest, ...]:
        job = self._repository.get(job_id)
        if job.state is not ContentJobState.APPROVED:
            raise PublicationValidationError("only an approved job can be published")
        if mode is PublicationMode.SCHEDULED:
            if scheduled_at is None or scheduled_at.tzinfo is None:
                raise PublicationValidationError("scheduled time must include a timezone")
            if scheduled_at <= self._clock():
                raise PublicationValidationError("scheduled time must be in the future")
        elif scheduled_at is not None:
            raise PublicationValidationError("publish-now cannot include a schedule")
        previews = self.preview_job(job_id)
        if any(not preview.ready for preview in previews):
            raise PublicationValidationError(
                "all requested platforms must be ready before publication"
            )
        now = self._clock()
        requests = []
        for preview in previews:
            schedule_key = scheduled_at.isoformat() if scheduled_at else "now"
            idempotency_key = ":".join(
                (
                    job_id,
                    preview.platform_content_id,
                    mode.value,
                    schedule_key,
                    "dry" if self.policy.dry_run else "delivery",
                )
            )
            publication = PublicationRequest(
                id=self._id_factory(),
                job_id=job_id,
                platform_content_id=preview.platform_content_id,
                platform=preview.platform,
                requested_by=requested_by,
                mode=mode,
                scheduled_at=scheduled_at,
                idempotency_key=idempotency_key,
                status=(
                    PublicationStatus.SCHEDULED
                    if mode is PublicationMode.SCHEDULED
                    else PublicationStatus.READY
                ),
                dry_run=self.policy.dry_run,
                claim_owner=None,
                claimed_at=None,
                lease_expires_at=None,
                created_at=now,
                updated_at=now,
            )
            requests.append(self._repository.add_publication(publication))
        self._record_job_event(
            job_id,
            "publication_scheduled" if mode is PublicationMode.SCHEDULED else "publication_created",
            {
                "publication_count": len(requests),
                "mode": mode.value,
                "dry_run": self.policy.dry_run,
            },
        )
        return tuple(requests)

    def attach_media(
        self,
        job_id: str,
        *,
        platform_content_id: str,
        media_type: MediaAssetType,
        source_url: str,
        order: int,
        alt_text: str | None,
    ) -> MediaAsset:
        job = self._repository.get(job_id)
        if job.state is not ContentJobState.APPROVED:
            raise PublicationValidationError("media can only be attached after approval")
        content = self._repository.get_platform_content(platform_content_id)
        if content.job_id != job_id or content.platform != "instagram":
            raise PublicationValidationError("media target does not belong to this Instagram job")
        if not _safe_media_url(source_url):
            raise PublicationValidationError(
                "media URL must be a public HTTPS URL without credentials"
            )
        asset = MediaAsset(
            id=self._id_factory(),
            job_id=job_id,
            platform_content_id=platform_content_id,
            media_type=media_type,
            source_url=source_url,
            order=order,
            alt_text=alt_text,
            created_at=self._clock(),
        )
        self._repository.add_media_asset(asset)
        self._record_job_event(
            job_id,
            "publication_media_attached",
            {"platform": "instagram", "media_order": order, "media_type": media_type.value},
        )
        return asset

    def cancel(self, publication_id: str, *, cancelled_by: str) -> PublicationRequest:
        publication = self._repository.get_publication(publication_id)
        if publication.status is not PublicationStatus.SCHEDULED:
            raise PublicationValidationError("only scheduled publication can be cancelled")
        updated = publication.transition(
            PublicationStatus.CANCELLED, now=self._clock(), clear_claim=True
        )
        self._repository.save_publication(updated)
        self._record_job_event(
            updated.job_id,
            "publication_cancelled",
            {"platform": updated.platform, "cancelled_by": cancelled_by},
        )
        return updated

    def recover_expired_claims(
        self, *, now: datetime | None = None
    ) -> tuple[PublicationRequest, ...]:
        recovered = self._repository.recover_expired_publications(now=now or self._clock())
        for publication in recovered:
            self._record_job_event(
                publication.job_id,
                "publication_needs_reconciliation",
                {
                    "platform": publication.platform,
                    "reason": "expired_work_lease",
                },
            )
        return recovered

    def claim_and_execute(self, publication_id: str, *, owner: str) -> PublicationRequest | None:
        now = self._clock()
        claimed = self._repository.claim_publication(
            publication_id,
            owner=owner,
            now=now,
            lease_expires_at=now + timedelta(seconds=self.policy.lease_seconds),
        )
        return None if claimed is None else self.execute(claimed.id, owner=owner)

    def execute(self, publication_id: str, *, owner: str) -> PublicationRequest:
        publication = self._repository.get_publication(publication_id)
        if publication.status is PublicationStatus.PUBLISHED:
            return publication
        if publication.claim_owner != owner or publication.status is not PublicationStatus.READY:
            raise PublicationValidationError("publication must hold an active work claim")
        job = self._repository.get(publication.job_id)
        if job.state not in {ContentJobState.APPROVED, ContentJobState.PUBLISHING}:
            raise PublicationValidationError("publication authorization is no longer valid")
        content = self._repository.get_platform_content(publication.platform_content_id)
        publisher = self._registry.get(publication.platform)
        assets = self._repository.list_media_assets(content.id)
        try:
            self._validate_content(content)
            prepared = publisher.prepare(content, assets)
        except PublicationError as exc:
            return self._fail_before_attempt(publication, exc)
        if not publication.dry_run:
            if not self.policy.demo_mode and not self.policy.external_delivery_enabled:
                return self._fail_before_attempt(
                    publication, PublicationUnavailableError("live publishing is disabled")
                )
            if not self.policy.demo_mode and not publisher.configured:
                return self._fail_before_attempt(
                    publication, PublicationUnavailableError("publisher is not configured")
                )
            self._ensure_job_publishing(job.id)
        now = self._clock()
        working = publication.transition(PublicationStatus.PUBLISHING, now=now)
        self._repository.save_publication(working)
        if publication.dry_run:
            self._add_attempt(
                working,
                publisher.adapter_name,
                PublicationAttemptOutcome.DRY_RUN,
                started=now,
            )
            completed = working.transition(
                PublicationStatus.DRY_RUN_COMPLETED,
                now=self._clock(),
                clear_claim=True,
            )
            self._repository.save_publication(completed)
            self._record_job_event(
                completed.job_id,
                "publication_dry_run_completed",
                {"platform": completed.platform, "external_delivery": False},
            )
            return completed

        receipts = list(self._repository.list_publication_receipts(working.id))
        receipt_by_index = {receipt.item_index: receipt for receipt in receipts}
        parent_remote_id = receipts[-1].remote_id if receipts else None
        for item in prepared.items:
            if item.index in receipt_by_index:
                parent_remote_id = receipt_by_index[item.index].remote_id
                continue
            for retry_index in range(self.policy.max_retries + 1):
                started = self._clock()
                try:
                    result = publisher.publish_item(
                        working,
                        item,
                        parent_remote_id=parent_remote_id,
                    )
                    receipt = PublicationReceipt(
                        id=self._id_factory(),
                        publication_id=working.id,
                        platform=working.platform,
                        item_index=item.index,
                        remote_id=result.remote_id,
                        remote_url=result.remote_url,
                        published_at=self._clock(),
                        adapter_name=publisher.adapter_name,
                        adapter_version=publisher.adapter_version,
                        status=result.status,
                        delivery_kind=item.kind,
                        metadata=result.metadata,
                    )
                    self._repository.add_publication_receipt(receipt)
                    self._add_attempt(
                        working,
                        publisher.adapter_name,
                        PublicationAttemptOutcome.SUCCEEDED,
                        started=started,
                        remote_identifier=result.remote_id,
                    )
                    parent_remote_id = result.remote_id
                    break
                except PublicationError as exc:
                    if exc.ambiguous:
                        self._add_attempt(
                            working,
                            publisher.adapter_name,
                            PublicationAttemptOutcome.AMBIGUOUS,
                            started=started,
                            error=exc,
                        )
                        return self._finish_with_status(
                            working, PublicationStatus.NEEDS_RECONCILIATION
                        )
                    if exc.retryable and retry_index < self.policy.max_retries:
                        self._add_attempt(
                            working,
                            publisher.adapter_name,
                            PublicationAttemptOutcome.RETRYABLE_FAILURE,
                            started=started,
                            error=exc,
                        )
                        self._record_job_event(
                            working.job_id,
                            "publication_retry",
                            {
                                "platform": working.platform,
                                "error_classification": exc.classification,
                                "retry_number": retry_index + 1,
                            },
                        )
                        self._sleeper(_retry_delay(exc, retry_index))
                        continue
                    self._add_attempt(
                        working,
                        publisher.adapter_name,
                        (
                            PublicationAttemptOutcome.RETRYABLE_FAILURE
                            if exc.retryable
                            else PublicationAttemptOutcome.PERMANENT_FAILURE
                        ),
                        started=started,
                        error=exc,
                    )
                    return self._finish_with_status(working, PublicationStatus.FAILED)
            else:
                raise AssertionError("bounded publication retry loop exited unexpectedly")
        completed = self._finish_with_status(working, PublicationStatus.PUBLISHED)
        self._sync_global_state(completed.job_id)
        return completed

    def reconcile(self, publication_id: str) -> PublicationRequest:
        publication = self._repository.get_publication(publication_id)
        if publication.status is not PublicationStatus.NEEDS_RECONCILIATION:
            raise PublicationValidationError("publication does not need reconciliation")
        publisher = self._registry.get(publication.platform)
        receipts = self._repository.list_publication_receipts(publication.id)
        content = self._repository.get_platform_content(publication.platform_content_id)
        prepared = publisher.prepare(content, self._repository.list_media_assets(content.id))
        locally_confirmed = len(receipts) == len(prepared.items)
        if locally_confirmed:
            result = ReconciliationResult(
                True,
                remote_id=receipts[-1].remote_id,
                remote_url=receipts[-1].remote_url,
            )
        else:
            result = publisher.reconcile(publication, receipts)
        if not result.confirmed or not result.remote_id:
            return publication
        if not locally_confirmed:
            item_index = len(receipts) + 1
            self._repository.add_publication_receipt(
                PublicationReceipt(
                    id=self._id_factory(),
                    publication_id=publication.id,
                    platform=publication.platform,
                    item_index=item_index,
                    remote_id=result.remote_id,
                    remote_url=result.remote_url,
                    published_at=self._clock(),
                    adapter_name=publisher.adapter_name,
                    adapter_version=publisher.adapter_version,
                    status="reconciled",
                    delivery_kind="reconciled",
                    metadata={"reconciled": True},
                )
            )
        completed = publication.transition(
            PublicationStatus.PUBLISHED, now=self._clock(), clear_claim=True
        )
        self._repository.save_publication(completed)
        self._record_job_event(
            completed.job_id,
            "publication_reconciled",
            {"platform": completed.platform, "confirmed": True},
        )
        self._sync_global_state(completed.job_id)
        return completed

    def _latest_contents(self, job_id: str):
        latest = {}
        for content in self._repository.list_platform_contents(job_id):
            current = latest.get(content.platform)
            if current is None or content.revision > current.revision:
                latest[content.platform] = content
        return latest

    def _validate_content(self, content) -> None:
        if content.validation_status is not PlatformValidationStatus.PASSED:
            raise PublicationValidationError("content has not passed deterministic validation")
        if (
            content.quality_score is None
            or content.quality_score < self.policy.minimum_quality_score
        ):
            raise PublicationValidationError("content does not meet the configured quality gate")

    def _add_attempt(
        self,
        publication: PublicationRequest,
        adapter_name: str,
        outcome: PublicationAttemptOutcome,
        *,
        started: datetime,
        error: PublicationError | None = None,
        remote_identifier: str | None = None,
    ) -> None:
        attempt_number = len(self._repository.list_publication_attempts(publication.id)) + 1
        self._repository.add_publication_attempt(
            PublicationAttempt(
                id=self._id_factory(),
                publication_id=publication.id,
                attempt_number=attempt_number,
                adapter_name=adapter_name,
                started_at=started,
                finished_at=self._clock(),
                outcome=outcome,
                error_classification=error.classification if error else None,
                remote_identifier=remote_identifier,
            )
        )

    def _finish_with_status(
        self, publication: PublicationRequest, status: PublicationStatus
    ) -> PublicationRequest:
        updated = publication.transition(status, now=self._clock(), clear_claim=True)
        self._repository.save_publication(updated)
        event = {
            PublicationStatus.PUBLISHED: "publication_succeeded",
            PublicationStatus.FAILED: "publication_failed",
            PublicationStatus.NEEDS_RECONCILIATION: "publication_needs_reconciliation",
        }[status]
        self._record_job_event(
            updated.job_id,
            event,
            {"platform": updated.platform, "status": updated.status.value},
        )
        return updated

    def _fail_before_attempt(
        self, publication: PublicationRequest, error: PublicationError
    ) -> PublicationRequest:
        updated = publication.transition(
            PublicationStatus.FAILED,
            now=self._clock(),
            clear_claim=True,
        )
        self._repository.save_publication(updated)
        self._record_job_event(
            updated.job_id,
            "publication_failed",
            {"platform": updated.platform, "error_classification": error.classification},
        )
        return updated

    def _ensure_job_publishing(self, job_id: str) -> None:
        job = self._repository.get(job_id)
        if job.state is ContentJobState.APPROVED:
            updated, step = self._state_machine.transition(
                job,
                ContentJobState.PUBLISHING,
                event="publication_started",
                details={"publication_mode": "demo" if self.policy.demo_mode else "live"},
            )
            self._repository.save(updated, step)

    def _sync_global_state(self, job_id: str) -> None:
        job = self._repository.get(job_id)
        if job.state is not ContentJobState.PUBLISHING:
            return
        publications = self._repository.list_publications(job_id)
        successful_platforms = {
            publication.platform
            for publication in publications
            if not publication.dry_run and publication.status is PublicationStatus.PUBLISHED
        }
        if set(job.target_platforms) <= successful_platforms:
            updated, step = self._state_machine.transition(
                job,
                ContentJobState.PUBLISHED,
                event="publication_job_completed",
                details={"platform_count": len(job.target_platforms)},
            )
            self._repository.save(updated, step)

    def _record_job_event(self, job_id: str, event: str, details: dict) -> None:
        job = self._repository.get(job_id)
        if job.state is ContentJobState.PUBLISHED:
            return
        updated, step = self._state_machine.record_event(job, event=event, details=details)
        self._repository.save(updated, step)


def _safe_error_message(error: PublicationError) -> str:
    labels = {
        "validation": "Contenu ou média non prêt pour la publication.",
        "unavailable": "Publication indisponible selon la configuration actuelle.",
        "authentication": "Authentification de publication non valide.",
        "permission": "Permission de publication insuffisante.",
    }
    return labels.get(error.classification, "Publication non prête.")


def _required_media_count(content) -> int:
    payload = content.payload
    slides = getattr(payload, "slides", None)
    if slides is not None:
        return len(slides)
    return 1 if content.platform == "instagram" else 0


def _safe_media_url(value: str) -> bool:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in {None, 443}
    ):
        return False
    if parsed.hostname.casefold() in {"localhost", "localhost.localdomain"}:
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return True
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    )


def _retry_delay(error: PublicationError, retry_index: int) -> float:
    if isinstance(error, PublicationRateLimitError) and error.retry_after_seconds is not None:
        return min(5.0, max(0.0, error.retry_after_seconds))
    return min(2.0, 0.25 * (2**retry_index))
