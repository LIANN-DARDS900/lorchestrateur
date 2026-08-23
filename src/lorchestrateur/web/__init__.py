"""Flask application factory for L'Orchestrateur."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from flask import Flask, render_template
from werkzeug.exceptions import HTTPException

from lorchestrateur.config import ConfigurationError, Settings
from lorchestrateur.persistence.contracts import ContentIntelligenceRepository, JobNotFoundError
from lorchestrateur.web.composition import compose_web_components
from lorchestrateur.web.routes import bp
from lorchestrateur.web.security import csrf_token, enforce_csrf


def create_app(
    settings: Settings | None = None,
    *,
    repository: ContentIntelligenceRepository | None = None,
    test_config: dict[str, Any] | None = None,
) -> Flask:
    selected_settings = settings or Settings.from_env()
    if (
        selected_settings.app_env.lower() == "production"
        and not selected_settings.web_secret_key
    ):
        raise ConfigurationError("WEB_SECRET_KEY is required when APP_ENV=production")
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=selected_settings.web_secret_key or secrets.token_hex(32),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=selected_settings.app_env.lower() == "production",
        MAX_CONTENT_LENGTH=1_000_000,
        QUALITY_THRESHOLD=selected_settings.platform_min_quality_score,
    )
    if test_config:
        app.config.update(test_config)

    logging.basicConfig(
        level=getattr(logging, selected_settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app.extensions["lorchestrateur_settings"] = selected_settings
    app.extensions["lorchestrateur_components"] = compose_web_components(
        selected_settings,
        repository=repository,
    )
    app.before_request(enforce_csrf)
    app.context_processor(lambda: {"csrf_token": csrf_token})
    app.context_processor(
        lambda: {
            "demo_mode": selected_settings.app_ai_mode == "demo",
            "app_env": selected_settings.app_env,
        }
    )
    app.register_blueprint(bp)

    @app.errorhandler(JobNotFoundError)
    def missing_job(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(404)
    def missing_page(_error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(400)
    def bad_request(error):
        message = getattr(error, "description", "La demande n’est pas valide.")
        return render_template("errors/400.html", message=message), 400

    @app.errorhandler(Exception)
    def safe_server_error(error):
        if isinstance(error, HTTPException):
            message = (
                "La requête dépasse la taille autorisée."
                if error.code == 413
                else "Cette action HTTP n’est pas disponible."
            )
            return render_template("errors/400.html", message=message), error.code
        app.logger.error("Unhandled web request failure type=%s", type(error).__name__)
        return render_template("errors/500.html"), 500

    return app
