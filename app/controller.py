from fastapi import APIRouter, FastAPI

from app.config import Settings
from app.modules.base import ApiModule


class ModuleController:
    def __init__(self, app: FastAPI, settings: Settings) -> None:
        self._app = app
        self._settings = settings
        self._api_router = APIRouter(prefix=settings.api_prefix)

    def register_module(self, module: ApiModule) -> None:
        self._api_router.include_router(module.build_router())

    def mount(self) -> None:
        self._app.include_router(self._api_router)
