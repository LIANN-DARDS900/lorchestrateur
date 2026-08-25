"""French, content-safe view models for the server-rendered application."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from lorchestrateur.analytics.metrics import FAMILY_LABELS
from lorchestrateur.analytics.service import AnalyticsService
from lorchestrateur.domain.analytics import AnalyticsRunOutcome
from lorchestrateur.domain.learning import (
    LearningRunStatus,
    RecommendationStatus,
)
from lorchestrateur.domain.platform_content import PlatformContentRecord
from lorchestrateur.domain.workflow import ContentJob, ContentJobState
from lorchestrateur.learning.service import LearningService
from lorchestrateur.persistence.contracts import (
    AnalyticsRepository,
    ArtifactNotFoundError,
    AutomationRepository,
    ContentIntelligenceRepository,
    LearningRepository,
)
from lorchestrateur.platforms.blog import BlogContentV1
from lorchestrateur.platforms.facebook import FacebookContentV1
from lorchestrateur.platforms.instagram import (
    InstagramCarouselV1,
    InstagramImagePostV1,
    InstagramReelV1,
)
from lorchestrateur.platforms.x import XContentV1
from lorchestrateur.publishing.service import PublicationService

STATUS_LABELS = {
    ContentJobState.CREATED: "Préparation",
    ContentJobState.RESEARCHING: "Sources",
    ContentJobState.STRATEGIZING: "Stratégie",
    ContentJobState.GENERATING_MASTER: "Contenu maître",
    ContentJobState.ADAPTING_PLATFORMS: "Adaptation des canaux",
    ContentJobState.VALIDATING: "Revue qualité",
    ContentJobState.AWAITING_APPROVAL: "En attente d’approbation",
    ContentJobState.APPROVED: "Approuvé",
    ContentJobState.PAUSED: "En pause",
    ContentJobState.FAILED: "Échec",
    ContentJobState.PUBLISHING: "Publication en cours",
    ContentJobState.PUBLISHED: "Publié",
}

PLATFORM_LABELS = {
    "blog": "Blog",
    "x": "X",
    "instagram": "Instagram",
    "facebook": "Facebook",
}

QUALITY_LABELS = {
    "structure": "Structure",
    "completeness": "Complétude",
    "platform_fit": "Adéquation au canal",
    "evidence_integrity": "Intégrité des sources",
    "content_hygiene": "Hygiène du contenu",
}

SOURCE_TYPE_LABELS = {
    "manual": "Note manuelle",
    "web": "Page web",
    "document": "Document",
    "interview": "Entretien",
    "dataset": "Jeu de données",
    "other": "Autre",
}

STATUS_MESSAGES = {
    "AI providers unavailable under current policy": (
        "Aucun fournisseur d’IA autorisé n’est actuellement disponible. "
        "Aucun service payant n’a été utilisé."
    ),
    "controlled repair budget exhausted": (
        "Le budget de réparation contrôlée est épuisé. Une intervention humaine est requise."
    ),
    "research evidence is not ready": (
        "Les sources revues ne sont pas encore suffisantes pour poursuivre."
    ),
}


def present_job(job: ContentJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "idea": job.idea,
        "state": job.state.value,
        "status": STATUS_LABELS[job.state],
        "status_tone": _status_tone(job.state),
        "platforms": [PLATFORM_LABELS.get(item, item.title()) for item in job.target_platforms],
        "platform_keys": job.target_platforms,
        "created_at": _format_datetime(job.created_at),
        "updated_at": _format_datetime(job.updated_at),
        "status_message": STATUS_MESSAGES.get(job.status_message, job.status_message),
        "repair_attempts": job.repair_attempts,
        "approval_ready": job.state
        in {ContentJobState.AWAITING_APPROVAL, ContentJobState.APPROVED},
    }


def dashboard_view(
    repository: AutomationRepository,
    analytics_service: AnalyticsService | None = None,
    learning_service: LearningService | None = None,
    *,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    jobs = tuple(
        job
        for job in repository.list_jobs()
        if workspace_id is None or job.workspace_id == workspace_id
    )
    job_ids = {job.id for job in jobs}
    counts = Counter(job.state for job in jobs)
    processing_states = {
        ContentJobState.CREATED,
        ContentJobState.RESEARCHING,
        ContentJobState.STRATEGIZING,
        ContentJobState.GENERATING_MASTER,
        ContentJobState.ADAPTING_PLATFORMS,
        ContentJobState.VALIDATING,
    }
    all_publications = (
        repository.list_publications() if hasattr(repository, "list_publications") else ()
    )
    publications = tuple(item for item in all_publications if item.job_id in job_ids)
    publication_counts = Counter(item.status.value for item in publications)
    analytics_runs = (
        tuple(item for item in repository.list_analytics_runs() if item.job_id in job_ids)
        if hasattr(repository, "list_analytics_runs")
        else ()
    )
    snapshots = (
        tuple(item for item in repository.list_metric_snapshots() if item.job_id in job_ids)
        if hasattr(repository, "list_metric_snapshots")
        else ()
    )
    analytics_jobs = []
    if analytics_service is not None:
        for job in jobs:
            if job.state is not ContentJobState.PUBLISHED:
                continue
            platforms = analytics_service.summarize_job(job.id)
            if any(any(metric.value is not None for metric in item.metrics) for item in platforms):
                analytics_jobs.append(
                    {
                        "job": present_job(job),
                        "platforms": [_present_platform_performance(item) for item in platforms],
                    }
                )
    actions: list[dict[str, str]] = []
    for job in jobs:
        if job.state is ContentJobState.AWAITING_APPROVAL:
            actions.append(
                {"kind": "review", "label": "Revue requise", "detail": job.idea, "job_id": job.id}
            )
        elif job.state in {ContentJobState.PAUSED, ContentJobState.FAILED}:
            actions.append(
                {
                    "kind": "attention",
                    "label": STATUS_LABELS[job.state],
                    "detail": job.idea,
                    "job_id": job.id,
                }
            )
        elif job.state is ContentJobState.RESEARCHING and not any(
            item.evidence_status.value == "reviewed" for item in repository.list_sources(job.id)
        ):
            actions.append(
                {
                    "kind": "source",
                    "label": "Source revue requise",
                    "detail": job.idea,
                    "job_id": job.id,
                }
            )
        if job.state is ContentJobState.APPROVED and "instagram" in job.target_platforms:
            instagram = repository.list_platform_contents(job.id, platform="instagram")
            if instagram and not repository.list_media_assets(instagram[-1].id):
                actions.append(
                    {
                        "kind": "media",
                        "label": "Média Instagram requis",
                        "detail": job.idea,
                        "job_id": job.id,
                    }
                )
    for publication in publications:
        if publication.status.value in {"failed", "needs_reconciliation"}:
            actions.append(
                {
                    "kind": "delivery",
                    "label": "Livraison à vérifier",
                    "detail": PLATFORM_LABELS.get(publication.platform, publication.platform),
                    "job_id": publication.job_id,
                }
            )
    recommendations = (
        repository.list_optimization_recommendations(workspace_id=workspace_id)
        if hasattr(repository, "list_optimization_recommendations")
        else ()
    )
    for item in recommendations:
        if item.status is RecommendationStatus.PROPOSED:
            actions.append(
                {
                    "kind": "learning",
                    "label": "Décision d’apprentissage",
                    "detail": item.rationale,
                    "job_id": "",
                }
            )
    return {
        "total": len(jobs),
        "processing": sum(counts[state] for state in processing_states),
        "awaiting": counts[ContentJobState.AWAITING_APPROVAL],
        "approved": counts[ContentJobState.APPROVED],
        "attention": counts[ContentJobState.PAUSED] + counts[ContentJobState.FAILED],
        "recent": [present_job(job) for job in jobs[:6]],
        "actions": actions[:8],
        "publication": {
            "scheduled": publication_counts["scheduled"],
            "publishing": publication_counts["publishing"],
            "published": publication_counts["published"],
            "failed": publication_counts["failed"],
            "reconciliation": publication_counts["needs_reconciliation"],
        },
        "analytics": {
            "tracked_receipts": len({item.publication_receipt_id for item in snapshots}),
            "snapshots": len(snapshots),
            "errors": sum(
                item.outcome
                in {
                    AnalyticsRunOutcome.FAILED,
                    AnalyticsRunOutcome.RATE_LIMITED,
                }
                for item in analytics_runs
            ),
            "latest_at": _format_datetime(max(item.collected_at for item in snapshots))
            if snapshots
            else None,
            "recent": analytics_jobs[:3],
        },
        "learning": {
            "enabled": bool(learning_service and learning_service.policy.enabled),
            "mode": (
                learning_service.policy.mode.value if learning_service is not None else "demo"
            ),
            "observations": len(repository.list_performance_observations())
            if hasattr(repository, "list_performance_observations")
            else 0,
            "proposed": sum(
                item.status is RecommendationStatus.PROPOSED for item in recommendations
            )
            if hasattr(repository, "list_optimization_recommendations")
            else 0,
            "active": len(
                repository.list_learning_profile_entries(
                    workspace_id=workspace_id, active_only=True
                )
            )
            if hasattr(repository, "list_learning_profile_entries")
            else 0,
        },
    }


def learning_overview_view(
    repository: LearningRepository,
    service: LearningService,
    *,
    workspace_id: str,
) -> dict[str, Any]:
    observations = repository.list_performance_observations()
    recommendations = repository.list_optimization_recommendations(workspace_id=workspace_id)
    observation_by_id = {item.id: item for item in observations}
    entries = repository.list_learning_profile_entries(workspace_id=workspace_id)
    runs = repository.list_learning_runs()
    return {
        "enabled": service.policy.enabled,
        "apply_enabled": service.policy.apply_accepted_learning,
        "mode": service.policy.mode.value,
        "mode_label": (
            "Données de démonstration" if service.policy.mode.value == "demo" else "Données réelles"
        ),
        "minimum_sample_size": service.policy.minimum_sample_size,
        "minimum_effect_percent": service.policy.minimum_effect_percent,
        "recommendations": [
            _present_learning_recommendation(item, observation_by_id.get(item.observation_id))
            for item in recommendations
        ],
        "observations": [_present_observation(item) for item in observations],
        "active_entries": [
            {
                "id": item.id,
                "platform": PLATFORM_LABELS.get(item.platform, item.platform.title()),
                "topic": item.topic_category,
                "objective": item.objective,
                "kind": _recommendation_kind_label(item.kind.value),
                "parameters": dict(item.parameters),
                "strength": _evidence_strength_label(item.evidence_strength.value),
                "expires_at": _format_datetime(item.expires_at),
                "active": item.active,
            }
            for item in entries
            if item.active
        ],
        "insufficient_runs": [
            {
                "platform": PLATFORM_LABELS.get(
                    item.cohort_a.platform, item.cohort_a.platform.title()
                ),
                "topic": item.cohort_a.topic_category,
                "objective": item.cohort_a.objective,
                "window": item.cohort_a.window_hours,
                "count_a": item.sample_count_a,
                "count_b": item.sample_count_b,
                "minimum": item.minimum_sample_size,
                "created_at": _format_datetime(item.started_at),
            }
            for item in reversed(runs)
            if item.status is LearningRunStatus.INSUFFICIENT_DATA
        ],
        "events": [
            {
                "event": _learning_event_label(item.event),
                "actor": item.actor,
                "created_at": _format_datetime(item.created_at),
            }
            for item in reversed(repository.list_learning_events())
        ][:12],
    }


def analytics_overview_view(
    repository: AnalyticsRepository,
    service: AnalyticsService,
    *,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    jobs = [
        job
        for job in repository.list_jobs()
        if job.state is ContentJobState.PUBLISHED
        and (workspace_id is None or job.workspace_id == workspace_id)
    ]
    job_ids = {job.id for job in jobs}
    contents = []
    for job in jobs:
        platforms = service.summarize_job(job.id)
        contents.append(
            {
                "job": present_job(job),
                "platforms": [_present_platform_performance(item) for item in platforms],
                "has_data": any(
                    any(metric.value is not None for metric in item.metrics) for item in platforms
                ),
            }
        )
    runs = tuple(item for item in repository.list_analytics_runs() if item.job_id in job_ids)
    snapshots = tuple(item for item in repository.list_metric_snapshots() if item.job_id in job_ids)
    return {
        "demo_mode": service.policy.demo_mode,
        "external_enabled": service.policy.external_collection_enabled,
        "published_count": len(jobs),
        "tracked_count": len({item.job_id for item in snapshots}),
        "snapshot_count": len(snapshots),
        "error_count": sum(
            item.outcome in {AnalyticsRunOutcome.FAILED, AnalyticsRunOutcome.RATE_LIMITED}
            for item in runs
        ),
        "latest_at": _format_datetime(max(item.collected_at for item in snapshots))
        if snapshots
        else None,
        "contents": contents,
    }


def analytics_job_view(
    repository: AnalyticsRepository,
    service: AnalyticsService,
    job: ContentJob,
) -> dict[str, Any]:
    publications = repository.list_publications(job.id)
    receipts = [
        receipt
        for publication in publications
        for receipt in repository.list_publication_receipts(publication.id)
    ]
    receipts_by_platform: dict[str, list] = {}
    for receipt in receipts:
        receipts_by_platform.setdefault(receipt.platform, []).append(receipt)
    platforms = []
    for performance in service.summarize_job(job.id):
        item = _present_platform_performance(performance)
        item["receipts"] = [
            {
                "id": receipt.id,
                "published_at": _format_datetime(receipt.published_at),
                "remote_url": receipt.remote_url,
                "remote_id": receipt.remote_id,
            }
            for receipt in receipts_by_platform.get(performance.platform, [])
        ]
        platforms.append(item)
    runs = repository.list_analytics_runs(job_id=job.id)
    return {
        "job": present_job(job),
        "demo_mode": service.policy.demo_mode,
        "external_enabled": service.policy.external_collection_enabled,
        "platforms": platforms,
        "runs": [
            {
                "platform": PLATFORM_LABELS.get(item.platform, item.platform.title()),
                "started_at": _format_datetime(item.started_at),
                "outcome": _analytics_outcome_label(item.outcome),
                "metrics_count": item.metrics_collected_count,
                "error": item.error_classification,
                "unavailable_count": len(item.unavailable_metric_keys),
            }
            for item in reversed(runs)
        ],
        "has_receipts": bool(receipts),
    }


PUBLICATION_STATUS_LABELS = {
    "draft": "Brouillon",
    "scheduled": "Programmée",
    "ready": "Prête",
    "publishing": "Publication en cours",
    "dry_run_completed": "Simulation terminée",
    "published": "Publiée",
    "failed": "Échec",
    "cancelled": "Annulée",
    "needs_reconciliation": "À réconcilier",
}


def publication_view(
    repository,
    service: PublicationService,
    job: ContentJob,
    *,
    minimum_quality_score: int,
) -> dict[str, Any]:
    previews = service.preview_job(job.id)
    publications = repository.list_publications(job.id)
    content_by_id = {item.id: item for item in repository.list_platform_contents(job.id)}
    return {
        "job": present_job(job),
        "dry_run": service.policy.dry_run,
        "demo_mode": service.policy.demo_mode,
        "external_enabled": service.policy.external_delivery_enabled,
        "all_ready": bool(previews) and all(item.ready for item in previews),
        "previews": [
            {
                "platform": item.platform,
                "label": PLATFORM_LABELS.get(item.platform, item.platform.title()),
                "content_id": item.platform_content_id,
                "revision": item.revision,
                "ready": item.ready,
                "destination": item.destination,
                "quality_score": item.quality_score,
                "warnings": item.warnings,
                "media_required": item.media_required,
                "media_attached": item.media_attached,
                "media": (
                    repository.list_media_assets(item.platform_content_id)
                    if item.platform_content_id
                    else ()
                ),
                "content": (
                    present_platform_content(
                        content_by_id[item.platform_content_id], minimum_quality_score
                    )
                    if item.platform_content_id in content_by_id
                    else None
                ),
            }
            for item in previews
        ],
        "publications": [
            {
                "id": item.id,
                "platform": item.platform,
                "label": PLATFORM_LABELS.get(item.platform, item.platform.title()),
                "status": item.status.value,
                "status_label": PUBLICATION_STATUS_LABELS[item.status.value],
                "scheduled_at": (
                    _format_datetime(item.scheduled_at) if item.scheduled_at else None
                ),
                "dry_run": item.dry_run,
                "requested_by": item.requested_by,
                "receipts": [
                    {
                        "remote_id": receipt.remote_id,
                        "remote_url": receipt.remote_url,
                        "published_at": _format_datetime(receipt.published_at),
                        "status": _receipt_status_label(receipt.status),
                        "kind": receipt.delivery_kind,
                        "index": receipt.item_index,
                    }
                    for receipt in repository.list_publication_receipts(item.id)
                ],
                "attempt_count": len(repository.list_publication_attempts(item.id)),
            }
            for item in reversed(publications)
        ],
    }


def workspace_view(
    repository: ContentIntelligenceRepository,
    job: ContentJob,
    *,
    minimum_quality_score: int,
) -> dict[str, Any]:
    sources = repository.list_sources(job.id)
    source_names = {source.id: source.title for source in sources}
    strategy = _optional(lambda: repository.get_strategy(job.id))
    master = _optional(lambda: repository.get_master_content(job.id))
    platform_records = repository.list_platform_contents(job.id)
    latest = _latest_platform_records(platform_records)
    platform_views = {
        key: present_platform_content(record, minimum_quality_score)
        for key, record in latest.items()
    }
    history = repository.list_steps(job.id)
    job_view = present_job(job)
    learning_context = (
        repository.get_job_learning_context(job.id)
        if hasattr(repository, "get_job_learning_context")
        else None
    )
    profile = (
        _optional(lambda: repository.get_workspace_profile(job.workspace_id))
        if hasattr(repository, "get_workspace_profile")
        else None
    )
    return {
        "job": job_view,
        "profile": (
            {
                "id": profile.id,
                "name": profile.display_name,
                "revision": profile.revision,
            }
            if profile is not None
            else None
        ),
        "sources": [
            {
                "id": source.id,
                "title": source.title,
                "url": source.url,
                "type": SOURCE_TYPE_LABELS[source.source_type.value],
                "excerpt": source.relevant_excerpt,
                "reviewed": source.evidence_status.value == "reviewed",
                "status": (
                    "Revue et autorisée"
                    if source.evidence_status.value == "reviewed"
                    else "Non revue"
                ),
                "retrieved_at": _format_datetime(source.retrieved_at),
                "knowledge_reused": source.metadata.get("provenance")
                == "approved_workspace_knowledge",
                "knowledge_item_id": source.metadata.get("knowledge_item_id"),
            }
            for source in sources
        ],
        "strategy": None
        if strategy is None
        else {
            "objective": strategy.objective,
            "audience": strategy.target_audience,
            "angle": strategy.angle,
            "tone": strategy.tone,
            "outcome": strategy.intended_outcome,
            "messages": [
                {
                    "text": message.message,
                    "sources": [source_names.get(item, item) for item in message.source_ids],
                }
                for message in strategy.key_messages
            ],
            "provider": _provider(strategy.generation_metadata),
        },
        "master": None
        if master is None
        else {
            "title": master.title,
            "summary": master.summary,
            "body": master.body,
            "key_points": master.key_points,
            "sources": [source_names.get(item, item) for item in master.source_ids],
            "provider": _provider(master.generation_metadata),
        },
        "platforms": platform_views,
        "quality": [platform_views[key] for key in job.target_platforms if key in platform_views],
        "missing_platforms": [
            PLATFORM_LABELS.get(key, key.title())
            for key in job.target_platforms
            if key not in platform_views
        ],
        "history": [
            {
                "event": _event_label(step.event),
                "state": STATUS_LABELS[step.to_state],
                "created_at": _format_datetime(step.created_at),
                "sequence": step.sequence,
            }
            for step in reversed(history)
        ],
        "learning": None
        if learning_context is None
        else {
            "topic": learning_context.topic_category,
            "objective": learning_context.objective,
            "enabled_for_job": learning_context.use_learning,
            "mode": learning_context.mode.value,
            "constraints": dict(learning_context.explicit_constraints),
            "applied_count": len(learning_context.applied_profile_entry_ids),
            "applied_entry_ids": learning_context.applied_profile_entry_ids,
        },
    }


def present_platform_content(
    record: PlatformContentRecord, minimum_quality_score: int
) -> dict[str, Any]:
    payload = record.payload
    common: dict[str, Any] = {
        "id": record.id,
        "platform": record.platform,
        "label": PLATFORM_LABELS.get(record.platform, record.platform.title()),
        "format": record.format,
        "revision": record.revision,
        "schema": record.schema_version,
        "provider": _provider(record.generation_metadata),
        "quality_score": record.quality_score,
        "quality_threshold": minimum_quality_score,
        "quality_passed": (
            record.quality_score is not None
            and record.quality_score >= minimum_quality_score
            and record.validation_status.value == "passed"
        ),
        "validation_status": record.validation_status.value,
        "issues": [issue.message for issue in record.validation_issues],
        "breakdown": []
        if record.quality_breakdown is None
        else [
            {"label": QUALITY_LABELS[key], "value": value}
            for key, value in record.quality_breakdown.to_mapping().items()
        ],
        "sources": list(payload.source_ids),
    }
    if isinstance(payload, BlogContentV1):
        common["content"] = {
            "kind": "blog",
            "title": payload.title,
            "excerpt": payload.excerpt,
            "introduction": payload.introduction,
            "sections": [section.to_mapping() for section in payload.sections],
            "conclusion": payload.conclusion,
            "cta": payload.cta,
            "seo_title": payload.seo_title,
            "meta_description": payload.meta_description,
            "slug": payload.slug_suggestion,
            "internal_links": payload.internal_link_suggestions,
        }
    elif isinstance(payload, XContentV1):
        common["content"] = {
            "kind": "x",
            "opening_hook": payload.opening_hook,
            "posts": [
                {"order": post.order, "text": post.text, "characters": len(post.text)}
                for post in payload.posts
            ],
            "cta": payload.cta,
        }
    elif isinstance(payload, InstagramCarouselV1):
        common["content"] = {
            "kind": "instagram_carousel",
            "hook": payload.hook,
            "slides": [slide.to_mapping() for slide in payload.slides],
            "caption": payload.caption,
            "cta": payload.cta,
        }
    elif isinstance(payload, InstagramReelV1):
        common["content"] = {
            "kind": "instagram_reel",
            "hook": payload.opening_hook,
            "beats": [beat.to_mapping() for beat in payload.beats],
            "caption": payload.caption,
            "cta": payload.cta,
        }
    elif isinstance(payload, InstagramImagePostV1):
        common["content"] = {
            "kind": "instagram_image",
            "hook": payload.hook,
            "visual_concept": payload.visual_concept,
            "caption": payload.caption,
            "cta": payload.cta,
        }
    elif isinstance(payload, FacebookContentV1):
        common["content"] = {
            "kind": "facebook",
            "opening": payload.opening,
            "body": payload.body,
            "cta": payload.cta,
            "link_context": payload.link_context_recommendation,
        }
    else:
        raise TypeError(f"unsupported platform payload: {type(payload).__name__}")
    return common


def _present_observation(observation) -> dict[str, Any]:
    return {
        "id": observation.id,
        "platform": PLATFORM_LABELS.get(observation.platform, observation.platform.title()),
        "metric": observation.metric_key,
        "window": observation.window_hours,
        "format_a": observation.cohort_a_format,
        "format_b": observation.cohort_b_format,
        "sample_a": observation.sample_count_a,
        "sample_b": observation.sample_count_b,
        "median_a": _format_decimal(observation.median_a),
        "median_b": _format_decimal(observation.median_b),
        "mean_a": _format_decimal(observation.mean_a),
        "mean_b": _format_decimal(observation.mean_b),
        "difference": _format_decimal(observation.relative_difference_percent),
        "strength": _evidence_strength_label(observation.evidence_strength.value),
        "strength_key": observation.evidence_strength.value,
        "breakdown": dict(observation.evidence_breakdown),
        "publication_count": len(observation.publication_ids),
        "snapshot_count": len(observation.snapshot_ids),
        "created_at": _format_datetime(observation.created_at),
        "demo": observation.mode.value == "demo",
    }


def _present_learning_recommendation(recommendation, observation) -> dict[str, Any]:
    return {
        "id": recommendation.id,
        "platform": PLATFORM_LABELS.get(recommendation.platform, recommendation.platform.title()),
        "topic": recommendation.topic_category,
        "objective": recommendation.objective,
        "kind": _recommendation_kind_label(recommendation.kind.value),
        "parameters": dict(recommendation.parameters),
        "rationale": recommendation.rationale,
        "strength": _evidence_strength_label(recommendation.evidence_strength.value),
        "status": recommendation.status.value,
        "status_label": {
            "proposed": "À décider",
            "accepted": "Acceptée",
            "rejected": "Refusée",
            "expired": "Expirée",
            "superseded": "Remplacée",
        }[recommendation.status.value],
        "created_at": _format_datetime(recommendation.created_at),
        "expires_at": _format_datetime(recommendation.expires_at),
        "decided_by": recommendation.decided_by,
        "potentially_outdated": recommendation.potentially_outdated,
        "observation": _present_observation(observation) if observation else None,
        "demo": recommendation.mode.value == "demo",
    }


def _recommendation_kind_label(value: str) -> str:
    return {
        "test_format": "Tester un format",
        "preserve_current_approach": "Conserver l’approche",
    }.get(value, value)


def _evidence_strength_label(value: str) -> str:
    return {
        "insufficient": "Données insuffisantes",
        "weak": "Faible",
        "moderate": "Modérée",
        "strong": "Forte",
    }.get(value, value)


def _learning_event_label(value: str) -> str:
    return {
        "learning_context_configured": "Périmètre d’apprentissage configuré",
        "learning_analysis_completed": "Analyse de cohortes terminée",
        "learning_recommendation_proposed": "Recommandation proposée",
        "learning_recommendation_accepted": "Recommandation acceptée",
        "learning_recommendation_rejected": "Recommandation refusée",
        "learning_recommendation_expired": "Recommandation expirée",
        "learning_profile_applied": "Profil approuvé appliqué au futur workflow",
    }.get(value, value)


def _format_decimal(value: Decimal) -> str:
    normalized = value.quantize(Decimal("0.1"))
    return f"{normalized:,.1f}".replace(",", " ").replace(".", ",")


def _latest_platform_records(
    records: Iterable[PlatformContentRecord],
) -> dict[str, PlatformContentRecord]:
    latest: dict[str, PlatformContentRecord] = {}
    for record in records:
        current = latest.get(record.platform)
        if current is None or record.revision > current.revision:
            latest[record.platform] = record
    return latest


def _optional(loader: Any) -> Any:
    try:
        return loader()
    except ArtifactNotFoundError:
        return None


def _provider(metadata: Any) -> dict[str, str] | None:
    if metadata is None:
        return None
    return {"name": metadata.provider, "model": metadata.model}


def _format_datetime(value: datetime) -> str:
    return value.astimezone().strftime("%d/%m/%Y · %H:%M")


def _status_tone(state: ContentJobState) -> str:
    if state in {ContentJobState.APPROVED, ContentJobState.PUBLISHED}:
        return "success"
    if state is ContentJobState.AWAITING_APPROVAL:
        return "review"
    if state in {ContentJobState.PAUSED, ContentJobState.FAILED}:
        return "danger"
    return "progress"


def _event_label(event: str) -> str:
    labels = {
        "research_started": "Collecte des sources démarrée",
        "source_evidence_added": "Source ajoutée",
        "research_completed": "Sources validées",
        "content_strategy_persisted": "Stratégie créée",
        "master_content_persisted": "Contenu maître créé",
        "platform_content_persisted": "Adaptation enregistrée",
        "platform_adaptation_completed": "Adaptations terminées",
        "platform_quality_gate_passed": "Contrôle qualité réussi",
        "human_approval_recorded": "Approbation humaine enregistrée",
        "human_revision_requested": "Modifications demandées",
        "publication_created": "Publication préparée",
        "publication_scheduled": "Publication programmée",
        "publication_cancelled": "Programmation annulée",
        "publication_started": "Publication démarrée",
        "publication_retry": "Nouvelle tentative de publication",
        "publication_succeeded": "Livraison confirmée",
        "publication_failed": "Échec de livraison",
        "publication_needs_reconciliation": "Réconciliation requise",
        "publication_job_completed": "Tous les canaux sont livrés",
        "publication_media_attached": "Média de publication attaché",
        "publication_dry_run_completed": "Simulation de publication terminée",
        "workflow_paused": "Workflow mis en pause",
        "workflow_failed": "Workflow en échec",
    }
    return labels.get(event, event.replace("_", " ").capitalize())


def _receipt_status_label(status: str) -> str:
    labels = {
        "delivered_demo": "Livraison démo confirmée",
        "published": "Publication confirmée",
        "exported": "Export confirmé",
        "reconciled": "Réconciliation confirmée",
    }
    return labels.get(status, status.replace("_", " ").capitalize())


def _present_platform_performance(performance) -> dict[str, Any]:
    return {
        "platform": performance.platform,
        "label": PLATFORM_LABELS.get(performance.platform, performance.platform.title()),
        "latest_at": _format_datetime(performance.latest_at) if performance.latest_at else None,
        "freshness": performance.freshness,
        "collection_status": performance.collection_status,
        "source_label": performance.source_label,
        "receipt_count": performance.receipt_count,
        "next_collection_at": (
            _format_datetime(performance.next_collection_at)
            if performance.next_collection_at
            else None
        ),
        "metrics": [
            {
                "key": item.definition.key,
                "label": item.definition.label,
                "description": item.definition.description,
                "family": FAMILY_LABELS[item.definition.family],
                "aggregation": item.definition.aggregation.value,
                "value": _format_metric(item.value),
                "available": item.value is not None,
                "change": _format_change(item.change),
                "history": [
                    {
                        "value": _format_metric(point.value),
                        "observed_at": _format_datetime(point.observed_at),
                    }
                    for point in item.history
                ],
                "chart_points": _chart_points(tuple(point.value for point in item.history)),
            }
            for item in performance.metrics
        ],
    }


def _format_metric(value: Decimal | None) -> str:
    if value is None:
        return "Indisponible"
    if value == value.to_integral_value():
        return f"{int(value):,}".replace(",", " ")
    return format(value.normalize(), "f").replace(".", ",")


def _format_change(value: Decimal | None) -> str | None:
    if value is None:
        return None
    prefix = "+" if value > 0 else ""
    return prefix + _format_metric(value)


def _chart_points(values: tuple[Decimal, ...]) -> str | None:
    if len(values) < 2:
        return None
    width = Decimal(600)
    height = Decimal(120)
    low = min(values)
    high = max(values)
    spread = high - low
    points = []
    for index, value in enumerate(values):
        x = width * Decimal(index) / Decimal(len(values) - 1)
        y = height / 2 if spread == 0 else height - ((value - low) / spread * height)
        points.append(f"{int(x)},{int(y)}")
    return " ".join(points)


def _analytics_outcome_label(outcome: AnalyticsRunOutcome) -> str:
    return {
        AnalyticsRunOutcome.RUNNING: "Synchronisation en cours",
        AnalyticsRunOutcome.SUCCEEDED: "Collecte réussie",
        AnalyticsRunOutcome.PARTIAL: "Collecte partielle",
        AnalyticsRunOutcome.FAILED: "Échec de collecte",
        AnalyticsRunOutcome.RATE_LIMITED: "Limite API",
        AnalyticsRunOutcome.UNAVAILABLE: "Métriques indisponibles",
    }[outcome]
