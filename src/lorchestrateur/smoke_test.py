"""Developer-only, opt-in real-provider smoke test that stops at approval readiness."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from lorchestrateur.ai.factory import create_production_providers
from lorchestrateur.ai.router import AIRouter
from lorchestrateur.application.service import OrchestrationService
from lorchestrateur.config import ConfigurationError, Settings
from lorchestrateur.domain.content import EvidenceStatus, SourceType
from lorchestrateur.domain.platform_content import QualityPolicy
from lorchestrateur.domain.workflow import ContentJobState, StateMachine
from lorchestrateur.persistence.memory import InMemoryContentJobRepository
from lorchestrateur.platforms.builtins import create_default_registry

TOPIC = "How automation reduces repetitive IT operations"
SOURCE_ID = "smoke-source-1"
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _load_environment(path: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Load simple KEY=VALUE entries without expansion and without mutating os.environ."""

    values = dict(environ)
    if not path.exists():
        return values
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, raw_value = line.partition("=")
        name = name.strip()
        if not separator or not _ENV_NAME.fullmatch(name):
            raise ConfigurationError(f"invalid .env entry on line {line_number}")
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values.setdefault(name, value)
    return values


def _eligible_provider_names(settings: Settings, providers: Sequence) -> tuple[str, ...]:
    by_name = {provider.name: provider for provider in providers}
    eligible: list[str] = []
    for name in settings.ai_provider_order:
        provider = by_name.get(name)
        if provider is None or not provider.is_configured or not provider.is_available():
            continue
        if provider.is_paid and not settings.allow_paid_ai:
            continue
        eligible.append(name)
    return tuple(eligible)


def _status(label: str, passed: bool, detail: str = "") -> None:
    suffix = f" ({detail})" if detail else ""
    print(f"{label:.<28} {'PASS' if passed else 'FAIL'}{suffix}")


def _provider_detail(metadata, first_eligible: str) -> str:
    provider = metadata.provider
    fallback = ", fallback" if provider != first_eligible else ""
    return f"{provider}/{metadata.model}{fallback}"


def _print_verbose(label: str, value) -> None:
    print(f"\n{label}:\n{json.dumps(value, ensure_ascii=False, indent=2)}")


def run_smoke_test(settings: Settings, *, verbose: bool = False) -> int:
    providers = create_production_providers(settings)
    eligible = _eligible_provider_names(settings, providers)

    print("L'ORCHESTRATEUR - REAL AI SMOKE TEST\n")
    print("Provider policy")
    print(f"Paid AI: {'ENABLED' if settings.allow_paid_ai else 'DISABLED'}")
    print(f"Provider order: {' -> '.join(settings.ai_provider_order)}")
    for provider in providers:
        configured = "configured" if provider.is_configured else "not configured"
        print(
            f"- {provider.name}: {provider.model or '(no model)'}; {configured}; "
            f"cost={provider.cost_class.value}"
        )
    print()

    if not eligible:
        sys.stdout.flush()
        print(
            "No configured provider is eligible under the current paid-AI policy. "
            "No external request was made.",
            file=sys.stderr,
        )
        return 2

    first_eligible = eligible[0]
    router = AIRouter(
        providers,
        provider_order=settings.ai_provider_order,
        allow_paid_ai=settings.allow_paid_ai,
    )
    repository = InMemoryContentJobRepository()
    quality_policy = QualityPolicy(settings.platform_min_quality_score)
    service = OrchestrationService(
        repository,
        StateMachine(),
        create_default_registry(),
        ai_router=router,
        quality_policy=quality_policy,
    )

    try:
        job = service.create_job(
            workspace_id="real-ai-smoke-test",
            idea=TOPIC,
            target_platforms=("blog", "x", "instagram", "facebook"),
        )
        service.begin_research(job.id)
        service.add_source(
            job.id,
            source_id=SOURCE_ID,
            title="Local smoke-test evidence",
            url=None,
            source_type=SourceType.MANUAL,
            relevant_excerpt=(
                "Automation can reduce repetitive IT operations when teams retain explicit "
                "controls, review boundaries, and traceable exception handling."
            ),
            evidence_status=EvidenceStatus.REVIEWED,
        )
        research = service.complete_research(job.id)
        _status("Research", not research.paused)
        if research.paused:
            return 1

        strategy = service.generate_content_strategy(job.id)
        if strategy.strategy is None:
            _status("Strategy", False, strategy.job.status_message or "paused")
            _report_provider_failure(repository, job.id)
            return 1
        strategy_meta = strategy.strategy.generation_metadata
        _status("Strategy", True, _provider_detail(strategy_meta, first_eligible))
        if verbose:
            _print_verbose(
                "Strategy",
                {
                    "objective": strategy.strategy.objective,
                    "target_audience": strategy.strategy.target_audience,
                    "angle": strategy.strategy.angle,
                    "tone": strategy.strategy.tone,
                    "key_messages": [item.message for item in strategy.strategy.key_messages],
                    "intended_outcome": strategy.strategy.intended_outcome,
                },
            )

        master = service.generate_master_content(job.id)
        if master.master_content is None:
            _status("Master Content", False, master.job.status_message or "paused")
            _report_provider_failure(repository, job.id)
            return 1
        master_meta = master.master_content.generation_metadata
        _status("Master Content", True, _provider_detail(master_meta, first_eligible))
        if verbose:
            _print_verbose(
                "Master Content",
                {
                    "title": master.master_content.title,
                    "summary": master.master_content.summary,
                    "body": master.master_content.body,
                    "key_points": master.master_content.key_points,
                    "source_ids": master.master_content.source_ids,
                },
            )

        adaptations = service.adapt_platforms(job.id)
        if adaptations.paused:
            _report_platform_status(adaptations.contents, first_eligible, passed=False)
            _report_provider_failure(repository, job.id)
            return 1
        _report_platform_status(adaptations.contents, first_eligible, passed=True)

        evaluation = service.evaluate_platform_adaptations(job.id)
        if evaluation.repair_requested and not evaluation.paused:
            _status("Controlled Repair", True, "one targeted attempt")
            repaired = service.adapt_platforms(job.id)
            if repaired.paused:
                _report_provider_failure(repository, job.id)
                return 1
            evaluation = service.evaluate_platform_adaptations(job.id)

        validation_passed = all(report.is_valid for report in evaluation.reports.values())
        quality_passed = all(
            content.is_approval_ready(quality_policy) for content in evaluation.contents.values()
        )
        _status("Validation", validation_passed)
        _status("Quality Gate", quality_passed)
        if verbose:
            for platform, content in evaluation.contents.items():
                _print_verbose(platform.title(), dict(content.payload.to_mapping()))

        print("\nFinal state:")
        print(evaluation.job.state.value.upper())
        if evaluation.job.state is not ContentJobState.AWAITING_APPROVAL:
            _report_provider_failure(repository, job.id)
            return 1
        print("No content was published.")
        return 0
    except Exception as exc:  # the command boundary must return a non-zero status
        print(f"Smoke test failed safely: {type(exc).__name__}", file=sys.stderr)
        return 1


def _report_platform_status(contents, first_eligible: str, *, passed: bool) -> None:
    for platform in ("blog", "x", "instagram", "facebook"):
        content = contents.get(platform)
        detail = (
            _provider_detail(content.generation_metadata, first_eligible)
            if content is not None
            else "no durable artifact"
        )
        _status(platform.title(), passed and content is not None, detail)


def _report_provider_failure(repository, job_id: str) -> None:
    steps = repository.list_steps(job_id)
    if not steps:
        return
    details = steps[-1].details
    attempts = details.get("attempts") if isinstance(details, Mapping) else None
    if attempts:
        summary = ", ".join(f"{item.get('provider')}={item.get('outcome')}" for item in attempts)
        print(f"Provider attempts: {summary}", file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        default=".env",
        help="local environment file to load without overriding process variables",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="display generated content in addition to stage metadata",
    )
    args = parser.parse_args(argv)
    try:
        environment = _load_environment(Path(args.env_file), os.environ)
        settings = Settings.from_env(environment)
    except (OSError, ConfigurationError) as exc:
        print(f"Smoke-test configuration error: {exc}", file=sys.stderr)
        return 2
    return run_smoke_test(settings, verbose=args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
