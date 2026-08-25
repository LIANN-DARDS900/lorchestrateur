"""Read-only projection of persisted orchestration state for HTML and polling JSON."""

from __future__ import annotations

from typing import Any

from lorchestrateur.domain.workflow import ContentJob, ContentJobState
from lorchestrateur.persistence.contracts import ArtifactNotFoundError, AutomationRepository
from lorchestrateur.web.presenters import PLATFORM_LABELS, STATUS_LABELS

_ORDER = {
    ContentJobState.CREATED: 0,
    ContentJobState.RESEARCHING: 1,
    ContentJobState.STRATEGIZING: 2,
    ContentJobState.GENERATING_MASTER: 3,
    ContentJobState.ADAPTING_PLATFORMS: 4,
    ContentJobState.VALIDATING: 5,
    ContentJobState.AWAITING_APPROVAL: 6,
    ContentJobState.APPROVED: 7,
    ContentJobState.PUBLISHING: 8,
    ContentJobState.PUBLISHED: 9,
}


def orchestration_status_view(
    repository: AutomationRepository,
    job: ContentJob,
    *,
    running: bool = False,
) -> dict[str, Any]:
    sources = repository.list_sources(job.id)
    reviewed_count = sum(item.evidence_status.value == "reviewed" for item in sources)
    strategy_present = _exists(lambda: repository.get_strategy(job.id))
    master_present = _exists(lambda: repository.get_master_content(job.id))
    latest_platforms: dict[str, object] = {}
    for item in repository.list_platform_contents(job.id):
        previous = latest_platforms.get(item.platform)
        if previous is None or item.revision > previous.revision:
            latest_platforms[item.platform] = item

    source_node = _artifact_node(
        job,
        key="sources",
        label="Sources",
        stage=ContentJobState.RESEARCHING,
        present=reviewed_count > 0,
        completed=_passed(job, ContentJobState.RESEARCHING),
        waiting_message="Ajoutez ou autorisez au moins une source revue.",
        completed_message=f"{reviewed_count} source(s) revue(s) autorisée(s).",
    )
    if job.state is ContentJobState.RESEARCHING and reviewed_count == 0:
        source_node = _node(
            "sources",
            "Sources",
            "paused",
            "Action requise : aucune source revue n’est encore disponible.",
        )
    nodes: list[dict[str, Any]] = [
        _node("idea", "Idée", "completed", "Brief stratégique enregistré."),
        source_node,
        _artifact_node(
            job,
            key="strategy",
            label="Stratégie",
            stage=ContentJobState.STRATEGIZING,
            present=strategy_present,
            completed=strategy_present,
            waiting_message="En attente de sources éligibles.",
            completed_message="Stratégie structurée et persistée.",
        ),
        _artifact_node(
            job,
            key="master",
            label="Contenu maître",
            stage=ContentJobState.GENERATING_MASTER,
            present=master_present,
            completed=master_present,
            waiting_message="En attente de la stratégie.",
            completed_message="Source éditoriale canonique persistée.",
        ),
    ]
    for platform in job.target_platforms:
        present = platform in latest_platforms
        nodes.append(
            _artifact_node(
                job,
                key=f"platform-{platform}",
                label=PLATFORM_LABELS.get(platform, platform.title()),
                stage=ContentJobState.ADAPTING_PLATFORMS,
                present=present,
                completed=present,
                waiting_message="Adaptation en attente.",
                completed_message="Adaptation typée persistée.",
            )
        )
    quality_completed = job.state in {
        ContentJobState.AWAITING_APPROVAL,
        ContentJobState.APPROVED,
        ContentJobState.PUBLISHING,
        ContentJobState.PUBLISHED,
    }
    nodes.append(
        _artifact_node(
            job,
            key="quality",
            label="Qualité",
            stage=ContentJobState.VALIDATING,
            present=quality_completed,
            completed=quality_completed,
            waiting_message="Contrôles déterministes en attente.",
            completed_message="Validation et seuil qualité franchis.",
        )
    )
    review_state = "neutral"
    review_message = "La décision humaine reste requise."
    if job.state is ContentJobState.AWAITING_APPROVAL:
        review_state = "in_progress"
        review_message = "Contenus prêts pour la revue humaine."
    elif job.state in {
        ContentJobState.APPROVED,
        ContentJobState.PUBLISHING,
        ContentJobState.PUBLISHED,
    }:
        review_state = "completed"
        review_message = "Approbation humaine enregistrée."
    elif job.state in {ContentJobState.PAUSED, ContentJobState.FAILED}:
        review_state = job.state.value
        review_message = "Une intervention est requise avant la revue."
    nodes.append(_node("review", "Revue humaine", review_state, review_message))

    requires_sources = job.state is ContentJobState.RESEARCHING and reviewed_count == 0
    terminal = requires_sources or job.state in {
        ContentJobState.AWAITING_APPROVAL,
        ContentJobState.APPROVED,
        ContentJobState.PAUSED,
        ContentJobState.FAILED,
        ContentJobState.PUBLISHED,
    }
    return {
        "job_id": job.id,
        "state": job.state.value,
        "status": STATUS_LABELS[job.state],
        "message": _safe_status_message(job),
        "running": running,
        "terminal": terminal,
        "poll_after_ms": 1500,
        "requires_sources": requires_sources,
        "nodes": nodes,
    }


def _node(key: str, label: str, state: str, message: str) -> dict[str, str]:
    group = (
        "platform"
        if key.startswith("platform-")
        else "tail"
        if key in {"quality", "review"}
        else "head"
    )
    return {"key": key, "label": label, "state": state, "message": message, "group": group}


def _artifact_node(
    job: ContentJob,
    *,
    key: str,
    label: str,
    stage: ContentJobState,
    present: bool,
    completed: bool,
    waiting_message: str,
    completed_message: str,
) -> dict[str, str]:
    if completed:
        return _node(key, label, "completed", completed_message)
    if job.state is stage:
        return _node(key, label, "in_progress", "Traitement gouverné en cours.")
    if job.state is ContentJobState.PAUSED and job.paused_from is stage:
        return _node(key, label, "paused", "Étape mise en pause en toute sécurité.")
    if job.state is ContentJobState.FAILED:
        return _node(key, label, "failed", "Le workflow s’est arrêté avant cette étape.")
    if present:
        return _node(key, label, "completed", completed_message)
    return _node(key, label, "neutral", waiting_message)


def _passed(job: ContentJob, stage: ContentJobState) -> bool:
    if job.state in {ContentJobState.PAUSED, ContentJobState.FAILED}:
        checkpoint = job.paused_from
        return checkpoint is not None and _ORDER.get(checkpoint, -1) > _ORDER[stage]
    return _ORDER.get(job.state, -1) > _ORDER[stage]


def _exists(loader) -> bool:
    try:
        loader()
    except ArtifactNotFoundError:
        return False
    return True


def _safe_status_message(job: ContentJob) -> str:
    if job.state is ContentJobState.PAUSED:
        if job.status_message == "AI providers unavailable under current policy":
            return (
                "Aucun fournisseur d’IA autorisé n’est actuellement disponible. "
                "Aucun service payant n’a été utilisé."
            )
        return "Le workflow est en pause et ses artefacts existants sont conservés."
    if job.state is ContentJobState.FAILED:
        return "Le workflow s’est arrêté sans exposer de détail technique."
    if job.state is ContentJobState.AWAITING_APPROVAL:
        return "Les canaux demandés sont prêts pour une décision humaine."
    if job.state is ContentJobState.APPROVED:
        return "L’approbation est enregistrée ; aucune publication automatique n’a lieu."
    return STATUS_LABELS[job.state]
