"""Durable, read-only analytics collection worker."""

from __future__ import annotations

import argparse
import logging
from time import sleep

from lorchestrateur.config import Settings
from lorchestrateur.web.composition import compose_web_components

LOGGER = logging.getLogger(__name__)


def run_once(settings: Settings, *, limit: int = 20) -> int:
    components = compose_web_components(settings)
    collected = components.analytics_service.collect_due(limit=limit)
    pruned = components.analytics_service.prune_retention()
    if pruned:
        LOGGER.info("analytics retention removed snapshots count=%s", pruned)
    return collected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="L'Orchestrateur analytics worker")
    parser.add_argument("--once", action="store_true", help="collect due metrics once and exit")
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    settings = Settings.from_env()
    logging.basicConfig(level=getattr(logging, settings.log_level, logging.INFO))
    if args.once:
        run_once(settings, limit=max(1, args.limit))
        return 0
    LOGGER.info(
        "analytics worker started mode=%s external_enabled=%s",
        settings.analytics_adapter_mode,
        settings.analytics_enabled,
    )
    while True:
        run_once(settings, limit=max(1, args.limit))
        sleep(settings.analytics_poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
