"""Request authentication: per-launch bearer token + origin/host checks + user sessions + roles."""

from __future__ import annotations

import hmac
from collections.abc import Callable
from typing import Any

from fastapi import Depends, HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from invoice_analytics.context import Engine
from invoice_analytics.security.users import AuthError, Session, SessionManager

SESSION_HEADER = "X-IA-Session"


class LaunchTokenMiddleware(BaseHTTPMiddleware):
    """Rejects any request without the per-launch token, from another origin, or with a foreign Host."""

    def __init__(self, app: Any, token: str, allowed_origins: tuple[str, ...]) -> None:
        super().__init__(app)
        self.token = token
        self.allowed_origins = set(allowed_origins)

    async def dispatch(self, request: Request, call_next: Callable[[Request], Any]) -> Response:
        origin = request.headers.get("origin")
        if origin and origin not in self.allowed_origins:
            return JSONResponse({"detail": "origin not allowed"}, status_code=403)
        host = (request.headers.get("host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "testserver"):
            return JSONResponse({"detail": "host not allowed"}, status_code=403)
        if request.method == "OPTIONS":  # CORS preflight carries no credentials
            return await call_next(request)  # type: ignore[no-any-return]
        if request.url.path == "/health":
            return await call_next(request)  # type: ignore[no-any-return]
        auth = request.headers.get("authorization", "")
        supplied = auth[7:] if auth.lower().startswith("bearer ") else request.query_params.get("token", "")
        if not self.token or not hmac.compare_digest(supplied.encode(), self.token.encode()):
            return JSONResponse({"detail": "invalid launch token"}, status_code=401)
        return await call_next(request)  # type: ignore[no-any-return]


def get_engine(request: Request) -> Engine:
    return request.app.state.engine  # type: ignore[no-any-return]


def get_sessions(request: Request) -> SessionManager:
    return request.app.state.sessions  # type: ignore[no-any-return]


def _session(request: Request) -> Session:
    tok = request.headers.get(SESSION_HEADER) or request.query_params.get("session")
    try:
        return get_sessions(request).get(tok)
    except AuthError as e:
        raise HTTPException(status_code=401, detail=str(e)) from e


def require(role: str) -> Callable[[Request], Session]:
    from invoice_analytics.security.users import ROLE_RANK

    def dep(request: Request) -> Session:
        s = _session(request)
        if ROLE_RANK[s.role] < ROLE_RANK[role]:
            raise HTTPException(status_code=403, detail=f"requires {role} role")
        return s

    return dep


Viewer = Depends(require("VIEWER"))
Reviewer = Depends(require("REVIEWER"))
Admin = Depends(require("ADMIN"))
