"""Small synchronizer-token CSRF boundary for the local server-rendered application."""

from __future__ import annotations

import secrets

from flask import abort, request, session

_SESSION_KEY = "_csrf_token"


def csrf_token() -> str:
    token = session.get(_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(32)
        session[_SESSION_KEY] = token
    return token


def enforce_csrf() -> None:
    if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
        return
    expected = session.get(_SESSION_KEY)
    supplied = request.form.get("csrf_token", "")
    if not isinstance(expected, str) or not secrets.compare_digest(expected, supplied):
        abort(400, description="La session du formulaire a expiré. Rechargez la page.")
