from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel

from app.dependencies import CurrentPrincipal
from app.modules.base import ApiModule
from app.services.ws_client import WsEventPublisher


class PingResponse(BaseModel):
    status: str
    principal_type: str
    principal_id: str
    server_time: datetime


class SystemModule(ApiModule):
    name = "system"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        self._ws_publisher = ws_publisher

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix="/system", tags=["system"])

        @router.get("/ping", response_model=PingResponse)
        def ping(principal: CurrentPrincipal) -> PingResponse:
            
            return PingResponse(
                status="ok",
                principal_type=principal.principal_type,
                principal_id=principal.principal_id,
                server_time=datetime.now(UTC),
            )

        return router
