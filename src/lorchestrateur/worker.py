"""Small durable SQLite publication worker for due local schedules."""

from __future__ import annotations

import argparse
import logging
import socket
from datetime import UTC, datetime, timedelta
from time import sleep

from lorchestrateur.config import Settings
from lorchestrateur.web.composition import compose_web_components

LOGGER = logging.getLogger(__name__)


def run_once(settings: Settings, *, owner: str | None = None, limit: int = 10) -> int:
    components = compose_web_components(settings)
    selected_owner = owner or f"worker:{socket.gethostname()}"
    now = datetime.now(UTC)
    recovered = components.publication_service.recover_expired_claims(now=now)
    for publication in recovered:
        LOGGER.warning(
            "expired publication lease requires reconciliation platform=%s",
            publication.platform,
        )
    claimed = components.repository.claim_due_publications(
        owner=selected_owner,
        now=now,
        lease_expires_at=now + timedelta(seconds=settings.publishing_lease_seconds),
        limit=limit,
    )
    for publication in claimed:
        try:
            components.publication_service.execute(publication.id, owner=selected_owner)
        except Exception as exc:  # worker boundary; service persisted classified failures
            LOGGER.error(
                "publication worker item failed type=%s platform=%s",
                type(exc).__name__,
                publication.platform,
            )
    return len(claimed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L'Orchestrateur publication worker")
    parser.add_argument("--once", action="store_true", help="process due work once and exit")
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
    if args.once:
        run_once(settings, limit=max(1, args.limit))
        return 0
    LOGGER.info(
        "publication worker started mode=%s dry_run=%s",
        settings.publishing_adapter_mode,
        settings.publishing_dry_run,
    )
    while True:
        run_once(settings, limit=max(1, args.limit))
        sleep(settings.publishing_poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
