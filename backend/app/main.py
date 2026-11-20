from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import get_current_user
from app.api.v1 import (
    admin,
    ai,
    assessments,
    auth,
    comparisons,
    features,
    overview,
    patients,
    processing,
    recordings,
    reports,
    stubs,
    timeline,
    users,
)
from app.core.config import get_settings
from app.db.mongo import ensure_indexes, get_db
from app.jobs import fail_stale_jobs


def create_app() -> FastAPI:
    settings = get_settings()
    settings.validate_for_production()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        ensure_indexes(get_db())
        fail_stale_jobs(get_db())
        yield

    app = FastAPI(
        lifespan=lifespan,
        title=settings.app_name,
        version=settings.app_version,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        if settings.is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response

    api = APIRouter(prefix="/api/v1")
    api.include_router(auth.router)
    api.include_router(admin.router)
    api.include_router(patients.router)
    api.include_router(users.router)
    api.include_router(recordings.router)
    api.include_router(processing.router)
    api.include_router(features.router)
    api.include_router(assessments.router)
    api.include_router(timeline.router)
    api.include_router(overview.router)
    api.include_router(comparisons.router)
    api.include_router(ai.router)
    api.include_router(reports.router)
    api.include_router(stubs.router)

    @api.get("/health", tags=["system"])
    def health():
        return {"status": "ok"}

    @api.get("/version", tags=["system"])
    def version(_=Depends(get_current_user)):
        return {"app": settings.app_version, "pipelines": {}, "models": {}}

    app.include_router(api)
    return app


app = create_app()
