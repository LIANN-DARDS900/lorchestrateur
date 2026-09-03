"""Local development entry point: python -m lorchestrateur.web."""

from __future__ import annotations

from lorchestrateur.config import Settings
from lorchestrateur.web import create_app


def main() -> None:
    settings = Settings.from_env()
    app = create_app(settings)
    mode = "DÉMONSTRATION" if settings.app_ai_mode == "demo" else "IA RÉELLE GOUVERNÉE"
    print("L’ORCHESTRATEUR — Content Orchestration Platform")
    print(f"Mode IA : {mode}")
    print(f"IA payante : {'AUTORISÉE' if settings.allow_paid_ai else 'DÉSACTIVÉE'}")
    print(f"URL locale : http://{settings.web_host}:{settings.web_port}")
    app.run(
        host=settings.web_host,
        port=settings.web_port,
        debug=False,
        use_reloader=False,
    )


if __name__ == "__main__":
    main()
