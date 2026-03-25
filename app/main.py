import logging
import inspect

from fastapi import FastAPI, HTTPException, Request
from fastapi import routing as fastapi_routing
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

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
