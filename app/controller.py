from fastapi import APIRouter, FastAPI

from app.config import Settings
from app.modules.base import ApiModule


class ModuleController:
    def __init__(self, app: FastAPI, settings: Settings) -> None:
        """Create a router orchestrator for registering API modules under one prefix."""
        self._app = app
        self._settings = settings
        self._api_router = APIRouter(prefix=settings.api_prefix)

    def register_module(self, module: ApiModule) -> None:
        """Register a module by including its router into the aggregate API router."""
        self._api_router.include_router(module.build_router())

    def mount(self) -> None:
        """Attach the aggregated API router to the FastAPI application instance."""
        self._app.include_router(self._api_router)
