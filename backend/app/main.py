"""FastAPI application entry point.

Responsibilities are intentionally narrow: wire middleware, mount the API
router, and translate exceptions into one normalized error shape.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.request_id import new_request_id
from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, log_event, request_id_var
from app.services.errors import (
    USER_MESSAGES,
    AppError,
    ErrorCode,
    code_for_field,
)

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    configure_logging("DEBUG" if settings.debug else "INFO")

    # Fail fast on a misconfigured production boot. A deploy that starts and
    # then silently runs against an empty SQLite file is far worse than one
    # that refuses to start and says which variable is missing.
    if settings.is_production:
        problems = settings.startup_problems()
        if problems:
            logger.error(
                "app.startup_rejected: %d blocking configuration problem(s)",
                len(problems),
            )
            for index, problem in enumerate(problems, 1):
                logger.error("app.startup_problem.%d: %s", index, problem)
            raise RuntimeError(
                "Refusing to start in production with an unsafe configuration. "
                "See the log for the specific variables to set."
            )
        for warning in settings.startup_warnings():
            logger.warning("app.startup_warning: %s", warning)

    from app.database.session import init_db, session_scope
    from app.services import auth_service

    init_db()
    with session_scope() as db:
        # Demo fixture only, and only ever on a genuinely empty database. The
        # previous version ran on every boot and re-created channels an
        # administrator had removed, and overwrote edited brand names, colours
        # and descriptions. Production never wants this path at all.
        if settings.effective_seed_demo_data:
            from app.database.seed import seed

            created = seed(db)
            if created:
                logger.warning(
                    "db.demo_data_seeded: %d demo row(s) inserted into an empty database",
                    created,
                )

        # Create the operator on first boot from APP_ADMIN_PASSWORD. There is
        # no default password: if the variable is unset this does nothing and
        # the login screen explains that no operator is configured.
        #
        # Idempotent, and it re-hashes when the configured password changes,
        # which is how rotating APP_ADMIN_PASSWORD actually takes effect.
        if settings.has_admin_password:
            auth_service.ensure_operator(
                db,
                username=settings.app_admin_username,
                password=settings.app_admin_password,
                display_name=settings.app_admin_display_name or settings.app_admin_username,
            )
        purge = auth_service.purge_expired_sessions(db)
        if purge:
            logger.info("auth.sessions_purged", extra={"context": {"removed": purge}})

    log_event(
        logger,
        "app.startup",
        environment=settings.app_env,
        n8n_configured=bool(settings.n8n_publish_webhook_url),
        database_url_scheme=settings.database_url.split("://", 1)[0],
        cors_origins=settings.cors_origin_list,
        # Booleans only - never the password, the key or the username.
        operator_configured=bool(settings.has_admin_password),
        auth_enabled=settings.auth_enabled,
    )
    yield
    log_event(logger, "app.shutdown")


def create_app() -> FastAPI:
    settings = get_settings()

    # Interactive docs publish the entire route surface, every schema and every
    # parameter name, and they sit outside the X-API-Key gate. Useful while
    # developing, an inventory for an attacker once the service is public.
    #
    # Deliberately keyed on APP_ENV alone and NOT re-enabled by DEBUG: DEBUG
    # defaults to true, so `is_production or debug` would have left /docs open
    # on any production deploy that forgot DEBUG=false.
    expose_docs = not settings.is_production

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "Application API for the social media publishing engine. "
            "This service owns accounts, channels and orchestration. "
            "Publishing itself is delegated to the existing n8n workflow."
        ),
        lifespan=lifespan,
        docs_url="/docs" if expose_docs else None,
        redoc_url="/redoc" if expose_docs else None,
        openapi_url="/openapi.json" if expose_docs else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )

    # Assign a request id to every request and echo it back for correlation.
    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or new_request_id()
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
            response.headers["X-Request-Id"] = request_id
            return response
        finally:
            request_id_var.reset(token)

    app.include_router(api_router, prefix=settings.api_prefix)

    # Internal service endpoint, mounted at the root on purpose.
    #
    # It has its own X-Internal-Secret gate and must NOT sit behind the
    # browser-facing X-API-Key, which the frontend does not have. It 404s
    # entirely unless INTERNAL_SERVICE_SECRET is configured.
    from app.api.internal import router as internal_router

    app.include_router(internal_router)

    # ------------------------------------------------------------------ #
    # Error normalisation: React sees one shape, always.
    # ------------------------------------------------------------------ #
    def _envelope(request_id: str, code: str, message: str, extra: list | None = None) -> dict:
        body = {
            "success": False,
            "request_id": request_id,
            "error": {"code": code, "message": message},
        }
        if extra:
            body["errors"] = extra
        return body

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        rid = request.headers.get("X-Request-Id") or request_id_var.get() or new_request_id()
        if exc.http_status >= 500:
            log_event(logger, "request.error", level=logging.ERROR, error_code=exc.code, path=request.url.path)
        else:
            log_event(logger, "request.rejected", level=logging.INFO, error_code=exc.code, path=request.url.path)
        body = _envelope(
            rid,
            exc.code,
            exc.user_message,
            [{"code": exc.code, "message": exc.user_message, "field": exc.field}] if exc.field else None,
        )
        return JSONResponse(status_code=exc.http_status, content=body, headers={"X-Request-Id": rid})

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        rid = request.headers.get("X-Request-Id") or request_id_var.get() or new_request_id()

        details: list[dict] = []
        seen: set[str] = set()
        for err in exc.errors():
            loc = err.get("loc") or []
            field = ".".join(str(p) for p in loc if p not in ("body", "query", "path")) or None
            code = code_for_field(field)
            if code in seen:
                continue
            seen.add(code)
            details.append({"code": code, "message": USER_MESSAGES[code], "field": field})

        primary = details[0]["code"] if details else ErrorCode.INVALID_REQUEST
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=_envelope(rid, primary, USER_MESSAGES[primary], details),
            headers={"X-Request-Id": rid},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        rid = request.headers.get("X-Request-Id") or request_id_var.get() or new_request_id()
        log_event(
            logger,
            "request.unhandled",
            level=logging.ERROR,
            error_type=type(exc).__name__,
            # Server-side only. The browser still receives a generic message.
            error_message=str(exc)[:500],
            path=request.url.path,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_envelope(rid, ErrorCode.INTERNAL_ERROR, USER_MESSAGES[ErrorCode.INTERNAL_ERROR]),
            headers={"X-Request-Id": rid},
        )

    return app


app = create_app()