"""Small bounded local coordinator for web-triggered workflow execution."""

from __future__ import annotations

import logging
from concurrent.futures import Future, ThreadPoolExecutor
from threading import RLock

from lorchestrateur.application.execution import ContentWorkflowExecutor
from lorchestrateur.application.service import OrchestrationService
from lorchestrateur.domain.workflow import ContentJobState
from lorchestrateur.persistence.contracts import AutomationRepository, ConcurrentUpdateError

logger = logging.getLogger(__name__)


class LocalWorkflowCoordinator:
    """Deduplicate work in one process while durable state stays authoritative."""

    def __init__(
        self,
        executor: ContentWorkflowExecutor,
        service: OrchestrationService,
        repository: AutomationRepository,
        *,
        maximum_workers: int = 2,
        run_inline: bool = False,
    ) -> None:
        if maximum_workers < 1 or maximum_workers > 8:
            raise ValueError("maximum_workers must be between one and eight")
        self._executor = executor
        self._service = service
        self._repository = repository
        self._run_inline = run_inline
        self._pool = (
            None
            if run_inline
            else ThreadPoolExecutor(
                max_workers=maximum_workers,
                thread_name_prefix="orchestration",
            )
        )
        self._futures: dict[str, Future[None]] = {}
        self._lock = RLock()

    def submit(self, job_id: str, *, human_guidance: str | None = None) -> bool:
        with self._lock:
            current = self._futures.get(job_id)
            if current is not None and not current.done():
                return False
            if self._run_inline:
                self._execute(job_id, human_guidance)
                return True
            assert self._pool is not None
            future = self._pool.submit(self._execute, job_id, human_guidance)
            self._futures[job_id] = future
            future.add_done_callback(lambda _future: self._forget(job_id))
            return True

    def is_running(self, job_id: str) -> bool:
        with self._lock:
            future = self._futures.get(job_id)
            return future is not None and not future.done()

    def wait(self, job_id: str, timeout: float = 10.0) -> None:
        with self._lock:
            future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)

    def shutdown(self, *, wait: bool = True) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=wait, cancel_futures=False)

    def _execute(self, job_id: str, human_guidance: str | None) -> None:
        try:
            self._executor.run(job_id, human_revision_guidance=human_guidance)
        except ConcurrentUpdateError:
            logger.info("workflow execution lost an optimistic concurrency race job_id=%s", job_id)
        except Exception as exc:  # Boundary: sanitize and preserve a durable failure state.
            logger.error(
                "workflow execution failed job_id=%s error_type=%s",
                job_id,
                type(exc).__name__,
            )
            try:
                job = self._repository.get(job_id)
                if job.state not in {ContentJobState.FAILED, ContentJobState.PUBLISHED}:
                    self._service.fail(job_id, reason="workflow execution failed safely")
            except Exception:
                logger.error("workflow failure checkpoint could not be persisted job_id=%s", job_id)

    def _forget(self, job_id: str) -> None:
        with self._lock:
            self._futures.pop(job_id, None)
