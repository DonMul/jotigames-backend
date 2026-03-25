import logging
import inspect

from fastapi import FastAPI, HTTPException, Request
from fastapi import routing as fastapi_routing
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.config import get_settings
from app.controller import ModuleController
from app.dependencies import resolve_request_locale
from app.modules import (
    AuthModule,
    BirdsOfPreyModule,
    BlindHikeModule,
    CheckpointHeistModule,
    CodeConspiracyModule,
    CourierRushModule,
    Crazy88Module,
    EchoHuntModule,
    ExplodingKittensModule,
    GameModule,
    GeoHunterModule,
    MarketCrashModule,
    PandemicResponseModule,
    ResourceRunModule,
    SuperAdminModule,
    SystemModule,
    TerritoryControlModule,
)
from app.services.i18n import translate_value
from app.services.ws_client import WsEventPublisher


fastapi_routing.asyncio.iscoroutinefunction = inspect.iscoroutinefunction


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Inject standard security response headers on every HTTP response."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
        return response


def create_app() -> FastAPI:
    """Create and configure the FastAPI application with all domain modules.

    Responsibilities:
    - initialize logging and app metadata
    - register global exception translators
    - instantiate websocket publisher and module controller
    - register all API modules and health endpoint
    """
    settings = get_settings()

    logging.basicConfig(level=settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
    )

    # -- Security middleware ------------------------------------------------
    app.add_middleware(SecurityHeadersMiddleware)

    cors_origins_raw = str(settings.cors_allowed_origins or "").strip()
    cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()] if cors_origins_raw else []
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept-Language"],
        max_age=600,
    )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        """Translate string-based HTTPException detail keys to localized messages.

        The backend keeps `detail` as stable translation-key identifiers while
        adding a localized `message` field for client display.
        """
        locale = resolve_request_locale(request)
        detail_value = exc.detail

        if isinstance(detail_value, str):
            translated = translate_value(detail_value, locale=locale)
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": detail_value, "message": translated},
                headers=exc.headers,
            )

        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": detail_value},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, _):
        """Normalize request validation errors to a stable localized payload."""
        locale = resolve_request_locale(request)
        detail_key = "validation.invalidRequest"
        return JSONResponse(
            status_code=422,
            content={
                "detail": detail_key,
                "message": translate_value(detail_key, locale=locale),
            },
        )

    ws_publisher = WsEventPublisher()
    controller = ModuleController(app=app, settings=settings)
    controller.register_module(AuthModule(ws_publisher=ws_publisher))
    controller.register_module(GameModule(ws_publisher=ws_publisher))
    controller.register_module(ExplodingKittensModule(ws_publisher=ws_publisher))
    controller.register_module(GeoHunterModule(ws_publisher=ws_publisher))
    controller.register_module(BlindHikeModule(ws_publisher=ws_publisher))
    controller.register_module(ResourceRunModule(ws_publisher=ws_publisher))
    controller.register_module(TerritoryControlModule(ws_publisher=ws_publisher))
    controller.register_module(MarketCrashModule(ws_publisher=ws_publisher))
    controller.register_module(Crazy88Module(ws_publisher=ws_publisher))
    controller.register_module(CourierRushModule(ws_publisher=ws_publisher))
    controller.register_module(EchoHuntModule(ws_publisher=ws_publisher))
    controller.register_module(CheckpointHeistModule(ws_publisher=ws_publisher))
    controller.register_module(PandemicResponseModule(ws_publisher=ws_publisher))
    controller.register_module(BirdsOfPreyModule(ws_publisher=ws_publisher))
    controller.register_module(CodeConspiracyModule(ws_publisher=ws_publisher))
    controller.register_module(SuperAdminModule())
    controller.register_module(SystemModule(ws_publisher=ws_publisher))
    controller.mount()

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        """Lightweight health probe used by uptime checks and orchestration."""
        return {"status": "ok"}

    return app


app = create_app()
