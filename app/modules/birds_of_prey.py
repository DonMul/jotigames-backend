from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.birds_of_prey_repository import BirdsOfPreyRepository
from app.services.birds_of_prey_service import BirdsOfPreyService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    overview: Dict[str, Any]


class DropEggRequest(BaseModel):
    egg_id: str = Field(min_length=1, max_length=64)


class DestroyEggRequest(BaseModel):
    egg_id: str = Field(min_length=1, max_length=64)
    points: int = Field(default=1, ge=0, le=1000)


class ActionResponse(BaseModel):
    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class BirdsOfPreyConfigResponse(BaseModel):
    config: Dict[str, Any]


class BirdsOfPreyConfigUpdateRequest(BaseModel):
    visibility_radius_meters: int = Field(default=100, ge=10, le=500)
    protection_radius_meters: int = Field(default=50, ge=5, le=500)
    auto_drop_seconds: int = Field(default=300, ge=30, le=7200)


class BirdsOfPreyModule(ApiModule, SharedModuleBase):
    name = "birds-of-prey"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        SharedModuleBase.__init__(self, game_type="birds_of_prey", ws_publisher=ws_publisher)
        self._service = BirdsOfPreyService()
        self._repository = BirdsOfPreyRepository()

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix="/birds-of-prey", tags=["birds-of-prey"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            state = self._service.get_team_bootstrap(db, game_id, team_id)
            state["config"] = self._repository.get_configuration(db, game_id)
            return TeamBootstrapResponse(state=state)

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/config",
            response_model=BirdsOfPreyConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get birds of prey config",
        )
        def get_config(game_id: str, principal: CurrentPrincipal, db: DbSession) -> BirdsOfPreyConfigResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return BirdsOfPreyConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.put(
            "/{game_id}/config",
            response_model=BirdsOfPreyConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update birds of prey config",
        )
        def update_config(
            game_id: str,
            body: BirdsOfPreyConfigUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> BirdsOfPreyConfigResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            values = {
                "visibility_radius_meters": int(body.visibility_radius_meters),
                "protection_radius_meters": int(body.protection_radius_meters),
                "auto_drop_seconds": int(body.auto_drop_seconds),
            }

            try:
                self._repository.update_configuration_without_commit(db, game_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="birds_of_prey.config.updateFailed") from error

            return BirdsOfPreyConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.post("/{game_id}/teams/{team_id}/egg/drop", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Drop egg")
        def drop_egg(game_id: str, team_id: str, body: DropEggRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.egg_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="birds_of_prey.validation.missingEggId")

            result = self._service.drop_egg(db, game_id=game_id, team_id=team_id, egg_id=body.egg_id.strip())
            
            return ActionResponse(
                success=result.success,
                message_key=self._localize_message_key(result.message_key, locale),
                action_id=result.action_id or None,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
            )

        @router.post("/{game_id}/teams/{team_id}/egg/destroy", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Destroy egg")
        def destroy_egg(game_id: str, team_id: str, body: DestroyEggRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.egg_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="birds_of_prey.validation.missingEggId")

            result = self._service.destroy_egg(
                db,
                game_id=game_id,
                team_id=team_id,
                egg_id=body.egg_id.strip(),
                points=body.points,
            )
            
            return ActionResponse(
                success=result.success,
                message_key=self._localize_message_key(result.message_key, locale),
                action_id=result.action_id or None,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
            )

        return router
