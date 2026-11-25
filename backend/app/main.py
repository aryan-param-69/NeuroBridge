import os
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import storage
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
from app.core.ratelimit import SlidingWindowLimiter, client_ip
from app.db.mongo import ensure_indexes, get_db
from app.jobs import fail_stale_jobs

_REQUEST_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")


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

    app.state.limiter = SlidingWindowLimiter()

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        path = request.url.path
        if settings.rate_limit_enabled and path.startswith("/api/") and not path.startswith("/api/v1/health"):
            auth_path = request.method == "POST" and path in ("/api/v1/auth/login", "/api/v1/auth/refresh")
            limit = settings.rate_limit_auth_per_minute if auth_path else settings.rate_limit_api_per_minute
            wait = app.state.limiter.check(f"{'auth' if auth_path else 'api'}:{client_ip(request, settings.trusted_proxies)}", limit)
            if wait is not None:
                return JSONResponse({"detail": "Too many requests from this address. Try again shortly."}, status_code=429,
                                    headers={"Retry-After": str(wait)})
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")
        rid = rid if _REQUEST_ID.fullmatch(rid) else uuid.uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
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
        """Liveness: the process is up. Use /health/ready to learn whether it can do its work."""
        return {"status": "ok"}

    @api.get("/health/ready", tags=["system"])
    def ready(response: Response):
        """Readiness: database reachable, storage writable, default model file present. Reports only ok/failed, never details."""
        checks = {}
        try:
            get_db().command("ping")
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "failed"
        checks["storage"] = "ok" if os.access(storage.root(), os.W_OK) else "failed"
        checks["model"] = "ok" if (Path(settings.model_dir) / settings.sedation_model / "model.joblib").is_file() else "failed"
        good = all(v == "ok" for v in checks.values())
        if not good:
            response.status_code = 503
        return {"status": "ready" if good else "degraded", "checks": checks}

    @api.get("/version", tags=["system"])
    def version(_=Depends(get_current_user)):
        return {"app": settings.app_version, "pipelines": {}, "models": {}}

    app.include_router(api)
    return app


app = create_app()
