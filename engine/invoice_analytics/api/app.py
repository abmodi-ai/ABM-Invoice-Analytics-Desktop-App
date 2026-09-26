"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from invoice_analytics import ENGINE_VERSION
from invoice_analytics.api.deps import SESSION_HEADER, LaunchTokenMiddleware
from invoice_analytics.api.routes import router
from invoice_analytics.context import Engine
from invoice_analytics.ingest.jobs import IngestJobs
from invoice_analytics.security.users import SessionManager


def create_app(eng: Engine) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if eng.config.start_workers:
            eng.jobs.start()
            app.state.ingest_jobs.start_watching()
        # warm the detection store off the request path
        import threading

        threading.Thread(target=eng.store.load_all, daemon=True, name="store-warm").start()
        yield
        app.state.ingest_jobs.stop()
        rt = eng.extras.get("llm_runtime")
        if rt is not None:
            rt.shutdown()

    app = FastAPI(
        title="ABM Invoice Analytics Engine",
        version=ENGINE_VERSION,
        lifespan=lifespan,
        description="Local engine API for the ABM Invoice Analytics desktop app. Loopback only.",
    )
    app.state.engine = eng
    app.state.sessions = SessionManager(eng.db, int(eng.settings.get("security.session_idle_seconds", 900)))
    app.state.ingest_jobs = IngestJobs(eng)
    app.add_middleware(LaunchTokenMiddleware, token=eng.config.token, allowed_origins=eng.config.allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(eng.config.allowed_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", SESSION_HEADER],
        expose_headers=["Content-Disposition"],
    )
    app.include_router(router)
    return app
