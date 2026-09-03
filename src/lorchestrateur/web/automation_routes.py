"""Automation-first HTTP routes attached to the existing web blueprint."""

from __future__ import annotations

from calendar import Calendar
from datetime import datetime
from zoneinfo import ZoneInfo

from flask import current_app, flash, jsonify, redirect, render_template, request, session, url_for

from lorchestrateur.application.automation import QuickCreateRequest
from lorchestrateur.domain.content import SourceType
from lorchestrateur.domain.workflow import ContentJobState
from lorchestrateur.persistence.contracts import ArtifactNotFoundError
from lorchestrateur.web.orchestration_presenter import orchestration_status_view
from lorchestrateur.web.presenters import (
    PLATFORM_LABELS,
    PUBLICATION_STATUS_LABELS,
    present_job,
)
from lorchestrateur.web.routes import (
    LOCAL_REVIEWER,
    SUPPORTED_PLATFORMS,
    _action_error,
    _automation_facade,
    _current_workspace_id,
    _learning_service,
    _repository,
    _service,
    _workflow_coordinator,
    _workspace_job,
    _workspace_service,
    bp,
)


@bp.post("/workspace/select")
def select_workspace():
    workspace_id = request.form.get("workspace_id", "").strip()
    if not any(item.id == workspace_id for item in _repository().list_workspace_profiles()):
        return _action_error("Ce projet n’est pas disponible.", 404)
    session["workspace_id"] = workspace_id
    return redirect(request.form.get("next") or url_for("web.dashboard"))


@bp.post("/preferences/expert-mode")
def toggle_expert_mode():
    value = request.form.get("enabled", "false")
    if value not in {"true", "false"}:
        return _action_error("La préférence d’affichage n’est pas valide.", 422)
    session["expert_mode"] = value == "true"
    flash(
        "Mode expert activé : les détails de gouvernance sont visibles."
        if value == "true"
        else "Mode essentiel activé.",
        "success",
    )
    return redirect(request.form.get("next") or url_for("web.dashboard"))


@bp.route("/content/new", methods=["GET", "POST"])
def new_content():
    errors: list[str] = []
    form = {
        "idea": request.form.get("idea", "").strip(),
        "platforms": request.form.getlist("platforms"),
        "topic_category": request.form.get("topic_category", "").strip(),
        "objective": request.form.get("objective", "").strip(),
        "audience": request.form.get("audience", "").strip(),
        "tone": request.form.get("tone", "").strip(),
        "cta": request.form.get("cta", "").strip(),
        "use_learning": (
            request.form.get("use_learning") == "yes"
            if request.form.get("use_learning") is not None
            or request.form.get("learning_override") == "present"
            else None
        ),
        "x_format": request.form.get("x_format", "auto").strip(),
    }
    if request.method == "POST":
        if len(form["idea"]) < 10:
            errors.append("Décrivez l’idée stratégique en au moins 10 caractères.")
        invalid = set(form["platforms"]) - set(SUPPORTED_PLATFORMS)
        if invalid:
            errors.append("Un canal sélectionné n’est pas pris en charge.")
        if not form["platforms"]:
            errors.append("Sélectionnez au moins un canal.")
        for key in ("topic_category", "objective", "audience", "tone", "cta"):
            if len(form[key]) > 500:
                errors.append("Un détail avancé dépasse la longueur autorisée.")
        if form["x_format"] not in {"auto", "single_post", "thread"}:
            errors.append("La contrainte de format X n’est pas valide.")
        if not errors:
            try:
                result = _automation_facade().prepare(
                    QuickCreateRequest(
                        workspace_id=_current_workspace_id(),
                        idea=form["idea"],
                        target_platforms=tuple(form["platforms"]),
                        objective=form["objective"] or None,
                        audience=form["audience"] or None,
                        tone=form["tone"] or None,
                        cta=form["cta"] or None,
                        topic_category=form["topic_category"] or None,
                        use_learning=form["use_learning"],
                        x_format=form["x_format"],
                    )
                )
            except ValueError:
                errors.append("Le contexte du projet ou les options avancées sont invalides.")
            else:
                if result.ready_to_execute:
                    _workflow_coordinator().submit(result.job.id)
                    flash(
                        f"Orchestration lancée avec {result.reused_source_count} "
                        "source(s) approuvée(s) du projet.",
                        "success",
                    )
                else:
                    flash(
                        "Action requise : ajoutez une source revue. Le brouillon est conservé.",
                        "warning",
                    )
                return redirect(url_for("web.orchestration_live", job_id=result.job.id))
    selected_profile = _repository().get_workspace_profile(_current_workspace_id())
    reusable_count = sum(
        item.eligible_for_reuse
        for item in _repository().list_workspace_knowledge(
            selected_profile.id, reusable_only=True, active_only=True
        )
    )
    return render_template(
        "new_content.html",
        errors=errors,
        form=form,
        platforms=PLATFORM_LABELS,
        learning_enabled=_learning_service().policy.enabled,
        profile=selected_profile,
        preparation={
            "objective": selected_profile.default_objective,
            "audience": selected_profile.default_audience,
            "tone": selected_profile.default_tone,
            "cta": selected_profile.default_cta,
            "source_count": reusable_count,
            "learning": (
                "Recommandations approuvées applicables"
                if _learning_service().policy.enabled
                else "Désactivé par la politique"
            ),
        },
    ), (422 if errors else 200)


@bp.get("/review")
def review_queue():
    workspace_id = _current_workspace_id()
    jobs = [
        present_job(job)
        for job in _repository().list_jobs()
        if job.workspace_id == workspace_id and job.state is ContentJobState.AWAITING_APPROVAL
    ]
    return render_template("review.html", jobs=jobs)


@bp.post("/review/approve-all")
def approve_all_ready():
    workspace_id = _current_workspace_id()
    ready = [
        job
        for job in _repository().list_jobs()
        if job.workspace_id == workspace_id and job.state is ContentJobState.AWAITING_APPROVAL
    ]
    if not ready:
        return _action_error("Aucun contenu entièrement validé n’est prêt à approuver.")
    for job in ready:
        _service().approve(job.id, approved_by=LOCAL_REVIEWER)
    flash(f"{len(ready)} contenu(s) validé(s) ont été approuvés.", "success")
    return redirect(url_for("web.review_queue"))


@bp.get("/calendar")
def publication_calendar():
    timezone = ZoneInfo(current_app.extensions["lorchestrateur_settings"].app_timezone)
    now = datetime.now(timezone)
    try:
        year = int(request.args.get("year", now.year))
        month = int(request.args.get("month", now.month))
    except ValueError:
        return _action_error("La période du calendrier n’est pas valide.", 422)
    if not 2020 <= year <= 2100 or not 1 <= month <= 12:
        return _action_error("La période du calendrier n’est pas valide.", 422)
    workspace_id = _current_workspace_id()
    events = []
    for publication in _repository().list_publications():
        job = _repository().get(publication.job_id)
        if job.workspace_id != workspace_id:
            continue
        instant = publication.scheduled_at or publication.created_at
        local = instant.astimezone(timezone)
        if (local.year, local.month) == (year, month):
            events.append(
                {
                    "day": local.day,
                    "time": local.strftime("%H:%M"),
                    "platform": PLATFORM_LABELS.get(publication.platform, publication.platform),
                    "status": PUBLICATION_STATUS_LABELS[publication.status.value],
                    "job": present_job(job),
                }
            )
    previous_month = 12 if month == 1 else month - 1
    previous_year = year - 1 if month == 1 else year
    next_month = 1 if month == 12 else month + 1
    next_year = year + 1 if month == 12 else year
    names = (
        "",
        "Janvier",
        "Février",
        "Mars",
        "Avril",
        "Mai",
        "Juin",
        "Juillet",
        "Août",
        "Septembre",
        "Octobre",
        "Novembre",
        "Décembre",
    )
    return render_template(
        "calendar.html",
        calendar_view={
            "year": year,
            "month": month,
            "month_name": names[month],
            "weeks": Calendar(firstweekday=0).monthdayscalendar(year, month),
            "events": events,
            "previous": {"year": previous_year, "month": previous_month},
            "next": {"year": next_year, "month": next_month},
        },
    )


@bp.get("/jobs/<job_id>/orchestration")
def orchestration_live(job_id: str):
    job = _workspace_job(job_id)
    model = orchestration_status_view(
        _repository(), job, running=_workflow_coordinator().is_running(job.id)
    )
    return render_template("orchestration.html", orchestration=model, job=present_job(job))


@bp.get("/jobs/<job_id>/orchestration-status")
def orchestration_status(job_id: str):
    job = _workspace_job(job_id)
    return jsonify(
        orchestration_status_view(
            _repository(), job, running=_workflow_coordinator().is_running(job.id)
        )
    )


@bp.post("/settings/projects")
def create_workspace_profile():
    platforms = tuple(request.form.getlist("default_platforms"))
    if not platforms or set(platforms) - set(SUPPORTED_PLATFORMS):
        return _action_error("Sélectionnez des canaux par défaut valides.", 422)
    try:
        profile = _workspace_service().create_profile(
            display_name=request.form.get("display_name", "").strip(),
            slug=request.form.get("slug", "").strip(),
            website_url=request.form.get("website_url", "").strip() or None,
            description=request.form.get("description", "").strip() or None,
            default_audience=request.form.get("default_audience", "").strip(),
            default_objective=request.form.get("default_objective", "").strip(),
            default_tone=request.form.get("default_tone", "").strip(),
            default_cta=request.form.get("default_cta", "").strip() or None,
            default_topic_category=request.form.get("default_topic_category", "").strip(),
            default_platforms=platforms,
            business_constraints=_lines(request.form.get("business_constraints", "")),
            forbidden_claims=_lines(request.form.get("forbidden_claims", "")),
            uncertain_claims=_lines(request.form.get("uncertain_claims", "")),
            reuse_approved_knowledge=request.form.get("reuse_approved_knowledge") == "yes",
        )
    except ValueError:
        return _action_error("Le profil projet contient une valeur invalide.", 422)
    session["workspace_id"] = profile.id
    flash("Le projet et ses règles éditoriales ont été créés.", "success")
    return redirect(url_for("web.settings_page"))


@bp.post("/settings/project")
def update_workspace_profile():
    workspace_id = _current_workspace_id()
    platforms = tuple(request.form.getlist("default_platforms"))
    if not platforms or set(platforms) - set(SUPPORTED_PLATFORMS):
        return _action_error("Sélectionnez des canaux par défaut valides.", 422)
    try:
        _workspace_service().update_profile(
            workspace_id,
            display_name=request.form.get("display_name", "").strip(),
            website_url=request.form.get("website_url", "").strip() or None,
            description=request.form.get("description", "").strip() or None,
            default_audience=request.form.get("default_audience", "").strip(),
            default_objective=request.form.get("default_objective", "").strip(),
            default_tone=request.form.get("default_tone", "").strip(),
            default_cta=request.form.get("default_cta", "").strip() or None,
            default_topic_category=request.form.get("default_topic_category", "").strip(),
            default_platforms=platforms,
            business_constraints=_lines(request.form.get("business_constraints", "")),
            forbidden_claims=_lines(request.form.get("forbidden_claims", "")),
            uncertain_claims=_lines(request.form.get("uncertain_claims", "")),
            reuse_approved_knowledge=request.form.get("reuse_approved_knowledge") == "yes",
        )
    except ValueError:
        return _action_error("Le profil projet contient une valeur invalide.", 422)
    flash("Les préférences du projet ont été mises à jour.", "success")
    return redirect(url_for("web.settings_page"))


@bp.post("/settings/knowledge")
def add_workspace_knowledge():
    try:
        source_type = SourceType(request.form.get("source_type", ""))
        _workspace_service().add_knowledge(
            workspace_id=_current_workspace_id(),
            title=request.form.get("title", "").strip(),
            relevant_excerpt=request.form.get("excerpt", "").strip(),
            source_type=source_type,
            url=request.form.get("url", "").strip() or None,
            reviewed=request.form.get("reviewed") == "yes",
            reusable=request.form.get("reusable") == "yes",
        )
    except ValueError:
        return _action_error("La connaissance projet contient une valeur invalide.", 422)
    flash("La source a été ajoutée à la base de connaissances du projet.", "success")
    return redirect(url_for("web.settings_page", _anchor="knowledge"))


@bp.post("/settings/knowledge/<item_id>/toggle")
def toggle_workspace_knowledge(item_id: str):
    value = request.form.get("active")
    if value not in {"true", "false"}:
        return _action_error("L’état de la source n’est pas valide.", 422)
    try:
        _workspace_service().set_knowledge_active(
            item_id,
            workspace_id=_current_workspace_id(),
            active=value == "true",
        )
    except (ArtifactNotFoundError, ValueError):
        return _action_error("Cette source n’appartient pas au projet courant.", 404)
    flash("La disponibilité de la source a été mise à jour.", "success")
    return redirect(url_for("web.settings_page", _anchor="knowledge"))


def _lines(value: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in value.splitlines() if line.strip())
