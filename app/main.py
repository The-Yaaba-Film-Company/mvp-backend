import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import (
    annotations,
    auth,
    entities,
    projects,
    reports,
    scenes,
    screenplays,
    search,
    suggestions,
    validation,
)
from app.config import Settings, get_settings
from app.db import create_sessionmaker
from app.sentry import capture_validation_failure, init_sentry, record_http_request


def _validation_detail(errors) -> str:
    parts = [f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in errors]
    return ", ".join(parts) if parts else "Validation error."


def _parse_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    init_sentry(settings)

    app = FastAPI(title="MPCampo API", version="0.1.0")
    app.state.settings = settings
    app.state.sessionmaker = create_sessionmaker(settings)

    @app.middleware("http")
    async def request_breadcrumb(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        record_http_request(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000, 3),
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError):
        capture_validation_failure(exc)
        return JSONResponse(
            status_code=422,
            content={"detail": _validation_detail(exc.errors())},
        )

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    app.include_router(auth.router, prefix="/api")
    app.include_router(projects.router, prefix="/api")
    app.include_router(screenplays.project_screenplays_router, prefix="/api")
    app.include_router(screenplays.screenplays_router, prefix="/api")
    app.include_router(scenes.screenplay_scenes_router, prefix="/api")
    app.include_router(scenes.scenes_router, prefix="/api")
    app.include_router(entities.project_entities_router, prefix="/api")
    app.include_router(entities.entities_router, prefix="/api")
    app.include_router(annotations.scene_annotations_router, prefix="/api")
    app.include_router(annotations.annotations_router, prefix="/api")
    app.include_router(reports.project_reports_router, prefix="/api")
    app.include_router(reports.screenplay_reports_router, prefix="/api")
    app.include_router(suggestions.scene_ai_run_router, prefix="/api")
    app.include_router(suggestions.scene_ai_router, prefix="/api")
    app.include_router(suggestions.suggestions_router, prefix="/api")
    app.include_router(validation.validation_router, prefix="/api")
    app.include_router(search.search_router, prefix="/api")

    # Added last so it sits outermost: preflight OPTIONS is answered before
    # any auth/session dependency or breadcrumb middleware runs. Credentials
    # require concrete origins — never a wildcard here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_parse_origins(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app


# app = create_app()