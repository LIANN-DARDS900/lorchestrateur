"""Thin HTTP controllers for the local L'Orchestrateur application."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from lorchestrateur.analytics.contracts import AnalyticsCooldownError
from lorchestrateur.domain.analytics import AnalyticsRunOutcome
from lorchestrateur.domain.content import EvidenceStatus, SourceType
from lorchestrateur.domain.publication import MediaAssetType, PublicationMode
from lorchestrateur.domain.workflow import ContentJobState, StateTransitionError
from lorchestrateur.persistence.contracts import (
    ArtifactNotFoundError,
    AutomationRepository,
    JobNotFoundError,
)
from lorchestrateur.publishing.contracts import PublicationError
from lorchestrateur.web.orchestration_presenter import orchestration_status_view
from lorchestrateur.web.presenters import (
    PLATFORM_LABELS,
    analytics_job_view,
    analytics_overview_view,
    dashboard_view,
    learning_overview_view,
    present_job,
    publication_view,
    workspace_view,
)

bp = Blueprint("web", __name__)
SUPPORTED_PLATFORMS = tuple(PLATFORM_LABELS)
LOCAL_WORKSPACE_ID = "local-workspace"
LOCAL_REVIEWER = "Responsable de contenu local"


def _repository() -> AutomationRepository:
    return current_app.extensions["lorchestrateur_components"].repository


def _service():
    return current_app.extensions["lorchestrateur_components"].service


def _publication_service():
    return current_app.extensions["lorchestrateur_components"].publication_service


def _analytics_service():
    return current_app.extensions["lorchestrateur_components"].analytics_service


def _learning_service():
    return current_app.extensions["lorchestrateur_components"].learning_service


def _workspace_service():
    return current_app.extensions["lorchestrateur_components"].workspace_service


def _automation_facade():
    return current_app.extensions["lorchestrateur_components"].automation_facade


def _workflow_coordinator():
    return current_app.extensions["lorchestrateur_components"].workflow_coordinator


def _current_workspace_id() -> str:
    selected = session.get("workspace_id", LOCAL_WORKSPACE_ID)
    if any(item.id == selected for item in _repository().list_workspace_profiles()):
        return selected
    session["workspace_id"] = LOCAL_WORKSPACE_ID
    return LOCAL_WORKSPACE_ID


def _workspace_job(job_id: str):
    job = _repository().get(job_id)
    selected_workspace = _current_workspace_id()
    if job.workspace_id != selected_workspace:
        try:
            _repository().get_workspace_profile(job.workspace_id)
        except ArtifactNotFoundError:
            # Pre-V1.1 jobs can reference a workspace that never had a profile.
            # They remain reachable from the local compatibility workspace only.
            if selected_workspace == LOCAL_WORKSPACE_ID:
                return job
        raise JobNotFoundError(job_id)
    return job


@bp.get("/")
def dashboard():
    workspace_id = _current_workspace_id()
    return render_template(
        "dashboard.html",
        dashboard=dashboard_view(
            _repository(),
            _analytics_service(),
            _learning_service(),
            workspace_id=workspace_id,
        ),
        quick_form={"idea": "", "platforms": ()},
        platforms=PLATFORM_LABELS,
    )


@bp.get("/learning")
def learning_overview():
    _learning_service().expire_due()
    return render_template(
        "learning.html",
        learning=learning_overview_view(
            _repository(), _learning_service(), workspace_id=_current_workspace_id()
        ),
    )


@bp.post("/learning/analyze")
def analyze_learning():
    try:
        window_hours = int(request.form.get("window_hours", ""))
        outcome = _learning_service().analyze(
            workspace_id=_current_workspace_id(),
            platform=request.form.get("platform", ""),
            topic_category=request.form.get("topic_category", ""),
            objective=request.form.get("objective", ""),
            window_hours=window_hours,
            actor=LOCAL_REVIEWER,
        )
    except (TypeError, ValueError):
        return _action_error("L’analyse est indisponible ou son périmètre n’est pas valide.", 422)
    if outcome.recommendation is None:
        flash(
            "Données insuffisantes : aucune recommandation n’a été créée.",
            "warning",
        )
    else:
        flash(
            "Observation calculée. La recommandation attend une décision humaine.",
            "success",
        )
    return redirect(url_for("web.learning_overview"))


@bp.post("/learning/recommendations/<recommendation_id>/accept")
def accept_learning_recommendation(recommendation_id: str):
    recommendation = _repository().get_optimization_recommendation(recommendation_id)
    if recommendation.workspace_id != _current_workspace_id():
        return _action_error("Cette recommandation n’appartient pas à cet espace.", 404)
    try:
        _learning_service().accept(
            recommendation_id,
            decided_by=LOCAL_REVIEWER,
            reason=request.form.get("reason", "").strip() or None,
        )
    except ValueError:
        return _action_error("Cette recommandation ne peut plus être acceptée.", 409)
    flash(
        "Recommandation acceptée. Elle pourra guider les futurs workflows compatibles.",
        "success",
    )
    return redirect(url_for("web.learning_overview"))


@bp.post("/learning/recommendations/<recommendation_id>/reject")
def reject_learning_recommendation(recommendation_id: str):
    recommendation = _repository().get_optimization_recommendation(recommendation_id)
    if recommendation.workspace_id != _current_workspace_id():
        return _action_error("Cette recommandation n’appartient pas à cet espace.", 404)
    try:
        _learning_service().reject(
            recommendation_id,
            decided_by=LOCAL_REVIEWER,
            reason=request.form.get("reason", "").strip() or None,
        )
    except ValueError:
        return _action_error("Cette recommandation ne peut plus être refusée.", 409)
    flash("Recommandation refusée. Aucun profil n’a été modifié.", "success")
    return redirect(url_for("web.learning_overview"))


@bp.get("/analytics")
def analytics_overview():
    return render_template(
        "analytics.html",
        analytics=analytics_overview_view(
            _repository(), _analytics_service(), workspace_id=_current_workspace_id()
        ),
    )


@bp.get("/jobs/<job_id>/analytics")
def job_analytics(job_id: str):
    job = _workspace_job(job_id)
    if job.state is not ContentJobState.PUBLISHED:
        return _action_error("Les analyses sont disponibles après une livraison confirmée.")
    return render_template(
        "job_analytics.html",
        analytics=analytics_job_view(_repository(), _analytics_service(), job),
    )


@bp.post("/jobs/<job_id>/analytics/refresh")
def refresh_job_analytics(job_id: str):
    job = _workspace_job(job_id)
    if job.state is not ContentJobState.PUBLISHED:
        return _action_error("Seul un contenu publié peut synchroniser ses métriques.")
    receipts = [
        receipt
        for publication in _repository().list_publications(job_id)
        for receipt in _repository().list_publication_receipts(publication.id)
    ]
    if not receipts:
        return _action_error("Aucun reçu de livraison exploitable n’est disponible.")
    collected = 0
    unavailable = 0
    cooldown = 0
    for receipt in receipts:
        try:
            run = _analytics_service().collect_receipt(receipt.id)
        except AnalyticsCooldownError:
            cooldown += 1
            continue
        if run.outcome in {AnalyticsRunOutcome.SUCCEEDED, AnalyticsRunOutcome.PARTIAL}:
            collected += 1
        else:
            unavailable += 1
    if collected:
        flash(
            f"Métriques actualisées pour {collected} livraison(s). "
            "Les observations historiques sont conservées.",
            "success",
        )
    elif cooldown:
        flash("Actualisation déjà récente : le délai de protection est encore actif.", "warning")
    else:
        flash(
            "Aucune nouvelle métrique n’est disponible avec la configuration actuelle.",
            "warning",
        )
    if unavailable and collected:
        flash(f"{unavailable} livraison(s) restent indisponibles.", "warning")
    return redirect(url_for("web.job_analytics", job_id=job_id))


@bp.get("/content")
def content_list():
    workspace_id = _current_workspace_id()
    jobs = [
        present_job(job) for job in _repository().list_jobs() if job.workspace_id == workspace_id
    ]
    return render_template("content_list.html", jobs=jobs)


@bp.get("/jobs/<job_id>")
def job_workspace(job_id: str):
    job = _workspace_job(job_id)
    model = workspace_view(
        _repository(),
        job,
        minimum_quality_score=current_app.config["QUALITY_THRESHOLD"],
    )
    return render_template(
        "workspace.html",
        workspace=model,
        orchestration=orchestration_status_view(
            _repository(), job, running=_workflow_coordinator().is_running(job.id)
        ),
    )


@bp.get("/jobs/<job_id>/publication")
def publication_workspace(job_id: str):
    job = _workspace_job(job_id)
    if job.state not in {
        ContentJobState.APPROVED,
        ContentJobState.PUBLISHING,
        ContentJobState.PUBLISHED,
    }:
        return _action_error("La publication n’est disponible qu’après l’approbation humaine.")
    model = publication_view(
        _repository(),
        _publication_service(),
        job,
        minimum_quality_score=current_app.config["QUALITY_THRESHOLD"],
    )
    settings = current_app.extensions["lorchestrateur_settings"]
    return render_template(
        "publication.html",
        publication=model,
        app_timezone=settings.app_timezone,
    )


@bp.post("/jobs/<job_id>/publication/media")
def attach_publication_media(job_id: str):
    _workspace_job(job_id)
    try:
        media_type = MediaAssetType(request.form.get("media_type", ""))
        order = int(request.form.get("order", ""))
    except (ValueError, TypeError):
        return _action_error("Le type ou l’ordre du média n’est pas valide.", 422)
    try:
        _publication_service().attach_media(
            job_id,
            platform_content_id=request.form.get("platform_content_id", ""),
            media_type=media_type,
            source_url=request.form.get("source_url", "").strip(),
            order=order,
            alt_text=request.form.get("alt_text", "").strip() or None,
        )
    except (PublicationError, ValueError) as exc:
        return _action_error(_publication_message(exc), 422)
    flash("Média Instagram attaché au contenu approuvé.", "success")
    return redirect(url_for("web.publication_workspace", job_id=job_id))


@bp.post("/jobs/<job_id>/publication/publish-now")
def publish_now(job_id: str):
    _workspace_job(job_id)
    if request.form.get("confirmed") != "yes":
        return _action_error("Confirmez explicitement cette action de publication.", 422)
    try:
        publications = _publication_service().create_publications(
            job_id,
            requested_by=LOCAL_REVIEWER,
            mode=PublicationMode.PUBLISH_NOW,
        )
        for publication in publications:
            _publication_service().claim_and_execute(publication.id, owner="web:publish-now")
    except PublicationError as exc:
        return _action_error(_publication_message(exc), 409)
    if _publication_service().policy.dry_run:
        flash("Simulation terminée. Aucun contenu externe n’a été publié.", "success")
    elif _publication_service().policy.demo_mode:
        flash("Livraison de démonstration terminée, sans plateforme externe.", "success")
    else:
        flash("Ordre de publication exécuté. Consultez les reçus de livraison.", "success")
    return redirect(url_for("web.publication_workspace", job_id=job_id))


@bp.post("/jobs/<job_id>/publication/schedule")
def schedule_publication(job_id: str):
    _workspace_job(job_id)
    raw_time = request.form.get("scheduled_at", "").strip()
    timezone_name = request.form.get("timezone", "").strip()
    settings = current_app.extensions["lorchestrateur_settings"]
    if timezone_name not in {settings.app_timezone, "UTC"}:
        return _action_error("Le fuseau horaire sélectionné n’est pas autorisé.", 422)
    try:
        scheduled_at = _resolve_local_time(raw_time, timezone_name)
        _publication_service().create_publications(
            job_id,
            requested_by=LOCAL_REVIEWER,
            mode=PublicationMode.SCHEDULED,
            scheduled_at=scheduled_at,
        )
    except (PublicationError, ValueError):
        return _action_error("Choisissez une date future valide avec un fuseau explicite.", 422)
    flash("La programmation durable a été enregistrée.", "success")
    return redirect(url_for("web.publication_workspace", job_id=job_id))


@bp.post("/jobs/<job_id>/publication/<publication_id>/cancel")
def cancel_publication(job_id: str, publication_id: str):
    _workspace_job(job_id)
    publication = _repository().get_publication(publication_id)
    if publication.job_id != job_id:
        return _action_error("Cette programmation n’appartient pas à ce workflow.", 404)
    try:
        _publication_service().cancel(publication_id, cancelled_by=LOCAL_REVIEWER)
    except PublicationError as exc:
        return _action_error(_publication_message(exc))
    flash("Programmation annulée. Aucun contenu distant n’a été supprimé.", "success")
    return redirect(url_for("web.publication_workspace", job_id=job_id))


@bp.post("/jobs/<job_id>/publication/<publication_id>/reconcile")
def reconcile_publication(job_id: str, publication_id: str):
    _workspace_job(job_id)
    publication = _repository().get_publication(publication_id)
    if publication.job_id != job_id:
        return _action_error("Cette publication n’appartient pas à ce workflow.", 404)
    try:
        _publication_service().reconcile(publication_id)
    except PublicationError as exc:
        return _action_error(_publication_message(exc))
    flash("Réconciliation exécutée sans nouvelle publication aveugle.", "success")
    return redirect(url_for("web.publication_workspace", job_id=job_id))


@bp.post("/jobs/<job_id>/sources")
def add_source(job_id: str):
    job = _workspace_job(job_id)
    if job.state is not ContentJobState.RESEARCHING:
        return _action_error("Les sources ne peuvent plus être modifiées à cette étape.")
    title = request.form.get("title", "").strip()
    excerpt = request.form.get("excerpt", "").strip()
    url = request.form.get("url", "").strip() or None
    type_value = request.form.get("source_type", SourceType.MANUAL.value)
    reviewed = request.form.get("reviewed") == "yes"
    if not title or not excerpt:
        return _action_error("Le titre et le résumé de la source sont obligatoires.", 422)
    if len(title) > 300 or len(excerpt) > 5000:
        return _action_error("La source dépasse la longueur autorisée.", 422)
    if url and not _valid_http_url(url):
        return _action_error("L’URL doit commencer par http:// ou https://.", 422)
    if request.form.get("reuse_in_workspace") == "yes" and not reviewed:
        return _action_error(
            "Une source doit être revue avant d’être réutilisable dans le projet.", 422
        )
    try:
        source_type = SourceType(type_value)
    except ValueError:
        return _action_error("Le type de source n’est pas valide.", 422)
    outcome = _service().add_source(
        job_id,
        title=title,
        relevant_excerpt=excerpt,
        source_type=source_type,
        url=url,
        evidence_status=(EvidenceStatus.REVIEWED if reviewed else EvidenceStatus.UNVERIFIED),
    )
    if request.form.get("reuse_in_workspace") == "yes":
        _workspace_service().promote_source(outcome.source)
    flash("Source ajoutée au workflow.", "success")
    return redirect(url_for("web.job_workspace", job_id=job_id, _anchor="sources"))


@bp.post("/jobs/<job_id>/launch")
def launch_job(job_id: str):
    job = _workspace_job(job_id)
    if job.state is not ContentJobState.RESEARCHING:
        return _action_error("Ce workflow ne peut pas être lancé depuis son état actuel.")
    started = _workflow_coordinator().submit(job_id)
    flash(
        "Orchestration lancée. Les étapes affichées proviennent de l’état persisté."
        if started
        else "Une orchestration est déjà en cours pour ce contenu.",
        "success" if started else "warning",
    )
    return redirect(url_for("web.orchestration_live", job_id=job_id))


@bp.post("/jobs/<job_id>/approve")
def approve_job(job_id: str):
    _workspace_job(job_id)
    try:
        _service().approve(job_id, approved_by=LOCAL_REVIEWER)
    except StateTransitionError:
        return _action_error(
            "L’approbation est impossible tant que tous les contenus requis ne sont pas valides."
        )
    flash("Approbation humaine enregistrée. Aucun contenu n’a été publié.", "success")
    return redirect(url_for("web.job_workspace", job_id=job_id))


@bp.post("/jobs/<job_id>/request-changes")
def request_changes(job_id: str):
    _workspace_job(job_id)
    reason = request.form.get("reason", "").strip()
    if len(reason) < 5:
        return _action_error("Précisez les modifications demandées.", 422)
    try:
        updated = _service().request_changes(
            job_id,
            requested_by=LOCAL_REVIEWER,
            reason=reason,
        )
    except StateTransitionError:
        return _action_error("Une demande de modifications n’est pas possible à cette étape.")
    if updated.state is ContentJobState.PAUSED:
        flash("Le budget de régénération est épuisé : le workflow est en pause.", "warning")
    else:
        _workflow_coordinator().submit(job_id, human_guidance=reason)
        flash("La révision contrôlée a été lancée.", "success")
    return redirect(url_for("web.orchestration_live", job_id=job_id))


@bp.post("/jobs/<job_id>/regenerate")
def regenerate_job(job_id: str):
    job = _workspace_job(job_id)
    if job.state is not ContentJobState.ADAPTING_PLATFORMS or job.repair_attempts < 1:
        return _action_error("Aucune régénération contrôlée n’est disponible.")
    _workflow_coordinator().submit(job_id)
    flash("La régénération contrôlée a été lancée.", "success")
    return redirect(url_for("web.orchestration_live", job_id=job_id))


@bp.get("/providers")
def providers():
    settings = current_app.extensions["lorchestrateur_settings"]
    providers_view = [
        {
            "name": "Gemini",
            "configured": bool(settings.gemini_api_key and settings.gemini_model),
            "model": settings.gemini_model or "Non défini",
            "cost": settings.gemini_cost_class.value,
            "enabled": settings.gemini_enabled,
        },
        {
            "name": "OpenRouter",
            "configured": bool(settings.openrouter_api_key and settings.openrouter_model),
            "model": settings.openrouter_model or "Non défini",
            "cost": settings.openrouter_cost_class.value,
            "enabled": settings.openrouter_enabled,
        },
    ]
    publishing_registry = current_app.extensions["lorchestrateur_components"].publishing_registry
    publishing_providers = [
        {
            "name": item.key.title() if item.key != "x" else "X",
            "configured": item.configured,
            "destination": item.destination_label,
            "adapter": item.adapter_name,
        }
        for item in publishing_registry.all()
    ]
    analytics_registry = current_app.extensions["lorchestrateur_components"].analytics_registry
    analytics_providers = [
        {
            "name": item.key.title() if item.key != "x" else "X",
            "configured": item.configured,
            "adapter": item.adapter_name,
        }
        for item in analytics_registry.all()
    ]
    return render_template(
        "providers.html",
        providers=providers_view,
        paid_enabled=settings.allow_paid_ai,
        provider_order=("démonstration locale",)
        if settings.app_ai_mode == "demo"
        else settings.ai_provider_order,
        demo_mode=settings.app_ai_mode == "demo",
        publishing_providers=publishing_providers,
        publishing_enabled=settings.publishing_enabled,
        publishing_dry_run=settings.publishing_dry_run,
        publishing_demo_mode=settings.publishing_adapter_mode == "demo",
        analytics_providers=analytics_providers,
        analytics_enabled=settings.analytics_enabled,
        analytics_demo_mode=settings.analytics_adapter_mode == "demo",
    )


@bp.get("/settings")
def settings_page():
    settings = current_app.extensions["lorchestrateur_settings"]
    workspace_id = _current_workspace_id()
    return render_template(
        "settings.html",
        quality_threshold=settings.platform_min_quality_score,
        app_ai_mode=settings.app_ai_mode,
        database_kind="SQLite local",
        publishing_mode=(
            "Démonstration" if settings.publishing_adapter_mode == "demo" else "Adaptateurs réels"
        ),
        publishing_policy=(
            "Simulation"
            if settings.publishing_dry_run
            else ("Activée" if settings.publishing_enabled else "Désactivée")
        ),
        app_timezone=settings.app_timezone,
        analytics_mode=(
            "Données de démonstration"
            if settings.analytics_adapter_mode == "demo"
            else "Adaptateurs réels"
        ),
        analytics_policy=(
            "Collecte externe activée"
            if settings.analytics_enabled
            else "Collecte externe désactivée"
        ),
        analytics_poll_seconds=settings.analytics_poll_seconds,
        analytics_retention_days=settings.analytics_retention_days,
        learning_mode=(
            "Données de démonstration" if settings.learning_mode == "demo" else "Données réelles"
        ),
        learning_policy=("Analyse activée" if settings.learning_enabled else "Analyse désactivée"),
        learning_apply_enabled=settings.learning_apply_enabled,
        learning_min_sample_size=settings.learning_min_sample_size,
        profile=_repository().get_workspace_profile(workspace_id),
        knowledge=_repository().list_workspace_knowledge(workspace_id),
        platforms=PLATFORM_LABELS,
    )


def _action_error(message: str, status: int = 409):
    flash(message, "error")
    return render_template("errors/action_error.html", message=message), status


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _resolve_local_time(raw_value: str, timezone_name: str) -> datetime:
    """Reject ambiguous/nonexistent wall times instead of choosing a DST fold silently."""

    local_time = datetime.fromisoformat(raw_value)
    if local_time.tzinfo is not None:
        raise ValueError("schedule input must be a local wall time")
    timezone = ZoneInfo(timezone_name)
    first = local_time.replace(tzinfo=timezone, fold=0)
    second = local_time.replace(tzinfo=timezone, fold=1)
    if first.utcoffset() != second.utcoffset():
        raise ValueError("ambiguous or nonexistent local time")
    if first.astimezone(UTC).astimezone(timezone).replace(tzinfo=None) != local_time:
        raise ValueError("nonexistent local time")
    return first


def _publication_message(error: Exception) -> str:
    classification = getattr(error, "classification", "validation")
    messages = {
        "validation": "La publication est bloquée par un contrôle de préparation.",
        "unavailable": "La publication est désactivée ou non configurée.",
        "authentication": "L’authentification de publication a été refusée.",
        "permission": "Le compte configuré ne possède pas la permission requise.",
        "rate_limit": "La limite de la plateforme a été atteinte.",
        "ambiguous_outcome": "Le résultat distant est incertain : réconciliation requise.",
    }
    return messages.get(classification, "La publication n’a pas pu être exécutée.")
