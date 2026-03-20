from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.blindhike_repository import BlindHikeRepository
from app.services.blindhike_service import BlindHikeService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    overview: Dict[str, Any]


class AddMarkerRequest(BaseModel):
    marker_id: str = Field(min_length=1, max_length=64)


class ActionResponse(BaseModel):
    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class BlindHikeConfigResponse(BaseModel):
    config: Dict[str, Any]


class BlindHikeConfigUpdateRequest(BaseModel):
    target_lat: Optional[float] = None
    target_lon: Optional[float] = None
    horizontal_flip: bool = False
    vertical_flip: bool = False
    scale_factor: float = Field(default=1.0, ge=0.1, le=10.0)
    rotation: int = Field(default=0, ge=0, le=360)
    max_markers: Optional[int] = Field(default=None, ge=1, le=100000)
    marker_cooldown: int = Field(default=0, ge=0, le=86400)


class BlindHikeModule(ApiModule, SharedModuleBase):
    name = "blindhike"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        SharedModuleBase.__init__(self, game_type="blindhike", ws_publisher=ws_publisher)
        self._service = BlindHikeService()
        self._repository = BlindHikeRepository()

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix="/blindhike", tags=["blindhike"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            return TeamBootstrapResponse(state=self._service.get_team_bootstrap(db, game_id, team_id))

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/config",
            response_model=BlindHikeConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get blindhike config",
        )
        def get_config(game_id: str, principal: CurrentPrincipal, db: DbSession) -> BlindHikeConfigResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return BlindHikeConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.put(
            "/{game_id}/config",
            response_model=BlindHikeConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update blindhike config",
        )
        def update_config(
            game_id: str,
            body: BlindHikeConfigUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> BlindHikeConfigResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            if body.target_lat is not None and (body.target_lat < -90 or body.target_lat > 90):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="blindhike.config.invalidTargetLat")
            if body.target_lon is not None and (body.target_lon < -180 or body.target_lon > 180):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="blindhike.config.invalidTargetLon")

            values: Dict[str, Any] = {
                "target_lat": body.target_lat,
                "target_lon": body.target_lon,
                "horizontal_flip": bool(body.horizontal_flip),
                "vertical_flip": bool(body.vertical_flip),
                "scale_factor": str(body.scale_factor),
                "rotation": int(body.rotation),
                "max_markers": body.max_markers,
                "marker_cooldown": int(body.marker_cooldown),
            }

            try:
                self._repository.update_configuration_without_commit(db, game_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="blindhike.config.updateFailed") from error

            return BlindHikeConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.post("/{game_id}/teams/{team_id}/marker/add", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Add marker")
        def add_marker(game_id: str, team_id: str, body: AddMarkerRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.marker_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="blindhike.validation.missingMarkerId")

            result = self._service.add_marker(db, game_id=game_id, team_id=team_id, marker_id=body.marker_id.strip())
            
            return ActionResponse(
                success=result.success,
                message_key=self._localize_message_key(result.message_key, locale),
                action_id=result.action_id or None,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
            )

        return router
