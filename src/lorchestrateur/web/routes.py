"""Thin HTTP controllers for the local L'Orchestrateur application."""

from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for

from lorchestrateur.domain.content import EvidenceStatus, SourceType
from lorchestrateur.domain.workflow import ContentJobState, StateTransitionError
from lorchestrateur.persistence.contracts import ContentIntelligenceRepository
from lorchestrateur.web.presenters import (
    PLATFORM_LABELS,
    dashboard_view,
    present_job,
    workspace_view,
)

bp = Blueprint("web", __name__)
SUPPORTED_PLATFORMS = tuple(PLATFORM_LABELS)
LOCAL_WORKSPACE_ID = "local-workspace"
LOCAL_REVIEWER = "Responsable de contenu local"


def _repository() -> ContentIntelligenceRepository:
    return current_app.extensions["lorchestrateur_components"].repository


def _service():
    return current_app.extensions["lorchestrateur_components"].service


def _executor():
    return current_app.extensions["lorchestrateur_components"].executor


@bp.get("/")
def dashboard():
    return render_template("dashboard.html", dashboard=dashboard_view(_repository()))


@bp.get("/content")
def content_list():
    jobs = [present_job(job) for job in _repository().list_jobs()]
    return render_template("content_list.html", jobs=jobs)


@bp.route("/content/new", methods=["GET", "POST"])
def new_content():
    errors: list[str] = []
    form = {
        "idea": request.form.get("idea", "").strip(),
        "platforms": request.form.getlist("platforms"),
    }
    if request.method == "POST":
        if len(form["idea"]) < 10:
            errors.append("Décrivez l’idée stratégique en au moins 10 caractères.")
        invalid = set(form["platforms"]) - set(SUPPORTED_PLATFORMS)
        if invalid:
            errors.append("Un canal sélectionné n’est pas pris en charge.")
        if not form["platforms"]:
            errors.append("Sélectionnez au moins un canal.")
        if not errors:
            job = _service().create_job(
                workspace_id=LOCAL_WORKSPACE_ID,
                idea=form["idea"],
                target_platforms=tuple(form["platforms"]),
            )
            _service().begin_research(job.id)
            flash("Le workflow est prêt. Ajoutez maintenant les sources autorisées.", "success")
            return redirect(url_for("web.job_workspace", job_id=job.id))
    return render_template(
        "new_content.html",
        errors=errors,
        form=form,
        platforms=PLATFORM_LABELS,
    ), (422 if errors else 200)


@bp.get("/review")
def review_queue():
    jobs = [
        present_job(job)
        for job in _repository().list_jobs()
        if job.state is ContentJobState.AWAITING_APPROVAL
    ]
    return render_template("review.html", jobs=jobs)


@bp.get("/jobs/<job_id>")
def job_workspace(job_id: str):
    job = _repository().get(job_id)
    model = workspace_view(
        _repository(),
        job,
        minimum_quality_score=current_app.config["QUALITY_THRESHOLD"],
    )
    return render_template("workspace.html", workspace=model)


@bp.post("/jobs/<job_id>/sources")
def add_source(job_id: str):
    job = _repository().get(job_id)
    if job.state is not ContentJobState.RESEARCHING:
        return _action_error("Les sources ne peuvent plus être modifiées à cette étape.")
    title = request.form.get("title", "").strip()
    excerpt = request.form.get("excerpt", "").strip()
    url = request.form.get("url", "").strip() or None
    type_value = request.form.get("source_type", SourceType.MANUAL.value)
    reviewed = request.form.get("reviewed") == "yes"
    if not title or not excerpt:
        return _action_error("Le titre et le résumé de la source sont obligatoires.", 422)
    if url and not _valid_http_url(url):
        return _action_error("L’URL doit commencer par http:// ou https://.", 422)
    try:
        source_type = SourceType(type_value)
    except ValueError:
        return _action_error("Le type de source n’est pas valide.", 422)
    _service().add_source(
        job_id,
        title=title,
        relevant_excerpt=excerpt,
        source_type=source_type,
        url=url,
        evidence_status=(EvidenceStatus.REVIEWED if reviewed else EvidenceStatus.UNVERIFIED),
    )
    flash("Source ajoutée au workflow.", "success")
    return redirect(url_for("web.job_workspace", job_id=job_id, _anchor="sources"))


@bp.post("/jobs/<job_id>/launch")
def launch_job(job_id: str):
    job = _repository().get(job_id)
    if job.state is not ContentJobState.RESEARCHING:
        return _action_error("Ce workflow ne peut pas être lancé depuis son état actuel.")
    result = _executor().run(job_id)
    if result.job.state is ContentJobState.AWAITING_APPROVAL:
        flash("Le contenu est prêt pour la revue humaine.", "success")
    elif result.job.state is ContentJobState.PAUSED:
        flash("La génération a été mise en pause en toute sécurité.", "warning")
    elif result.job.state is ContentJobState.FAILED:
        flash("Le workflow s’est arrêté sans exposer de détail technique.", "error")
    return redirect(url_for("web.job_workspace", job_id=job_id))


@bp.post("/jobs/<job_id>/approve")
def approve_job(job_id: str):
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
        result = _executor().run(job_id, human_revision_guidance=reason)
        if result.job.state is ContentJobState.AWAITING_APPROVAL:
            flash("La nouvelle révision est prête pour approbation.", "success")
        else:
            flash("La régénération s’est arrêtée à une limite gouvernée.", "warning")
    return redirect(url_for("web.job_workspace", job_id=job_id))


@bp.post("/jobs/<job_id>/regenerate")
def regenerate_job(job_id: str):
    job = _repository().get(job_id)
    if job.state is not ContentJobState.ADAPTING_PLATFORMS or job.repair_attempts < 1:
        return _action_error("Aucune régénération contrôlée n’est disponible.")
    result = _executor().run(job_id)
    if result.job.state is ContentJobState.AWAITING_APPROVAL:
        flash("La nouvelle révision est prête pour approbation.", "success")
    else:
        flash("La régénération s’est arrêtée à une limite gouvernée.", "warning")
    return redirect(url_for("web.job_workspace", job_id=job_id))


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
    return render_template(
        "providers.html",
        providers=providers_view,
        paid_enabled=settings.allow_paid_ai,
        provider_order=("démonstration locale",)
        if settings.app_ai_mode == "demo"
        else settings.ai_provider_order,
        demo_mode=settings.app_ai_mode == "demo",
    )


@bp.get("/settings")
def settings_page():
    settings = current_app.extensions["lorchestrateur_settings"]
    return render_template(
        "settings.html",
        quality_threshold=settings.platform_min_quality_score,
        app_ai_mode=settings.app_ai_mode,
        database_kind="SQLite local",
    )


def _action_error(message: str, status: int = 409):
    flash(message, "error")
    return render_template("errors/action_error.html", message=message), status


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
