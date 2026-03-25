from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.birds_of_prey_repository import BirdsOfPreyRepository
from app.services.birds_of_prey_service import BirdsOfPreyService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    """Response payload containing team bootstrap state."""

    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    """Response payload containing admin overview state."""

    overview: Dict[str, Any]


class DropEggRequest(BaseModel):
    """Request body for dropping an egg."""

    egg_id: str = Field(default="", max_length=64)


class DestroyEggRequest(BaseModel):
    """Request body for destroying an enemy egg."""

    egg_id: str = Field(min_length=1, max_length=64)


class TeamLocationUpdateRequest(BaseModel):
    """Request body containing new team location coordinates."""

    latitude: float
    longitude: float


class ActionResponse(BaseModel):
    """Standardized action response for birds-of-prey actions."""

    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class TeamLocationUpdateResponse(BaseModel):
    """Response for location updates including visibility recalculation."""

    success: bool
    message_key: str
    location: Dict[str, Any]
    visible_enemy_eggs: list[Dict[str, Any]]


class BirdsOfPreyConfigResponse(BaseModel):
    """Response wrapper around Birds of Prey configuration."""

    config: Dict[str, Any]


class BirdsOfPreyConfigUpdateRequest(BaseModel):
    """Request payload for updating Birds of Prey configuration."""

    visibility_radius_meters: int = Field(default=100, ge=10, le=500)
    protection_radius_meters: int = Field(default=50, ge=5, le=500)
    auto_drop_seconds: int = Field(default=300, ge=30, le=7200)


class BirdsOfPreyModule(ApiModule, SharedModuleBase):
    """FastAPI module for Birds of Prey gameplay and admin APIs."""

    name = "birds-of-prey"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        """Initialize module dependencies and shared settings."""
        SharedModuleBase.__init__(self, game_type="birds_of_prey", ws_publisher=ws_publisher)
        self._service = BirdsOfPreyService()
        self._repository = BirdsOfPreyRepository()

    @staticmethod
    def _build_egg_event_payload(game_id: str, egg: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize egg data into WS-safe event payload fields."""
        return {
            "game_id": game_id,
            "id": str(egg.get("id") or ""),
            "owner_team_id": str(egg.get("owner_team_id") or ""),
            "owner_team_name": str(egg.get("owner_team_name") or ""),
            "lat": egg.get("lat"),
            "lon": egg.get("lon"),
            "dropped_at": str(egg.get("dropped_at") or ""),
            "automatic": bool(egg.get("automatic")),
        }

    def _publish_team_score_event(self, *, game_id: str, team_id: str, score: int) -> None:
        """Publish score updates to shared, admin, and team-specific channels."""
        payload = {
            "game_id": game_id,
            "team_id": team_id,
            "score": int(score),
        }
        self._ws_publisher.publish(
            "game.birds_of_prey.team.score",
            payload,
            channels=[f"channel:{game_id}"],
        )
        self._ws_publisher.publish(
            "admin.birds_of_prey.team.score",
            payload,
            channels=[f"channel:{game_id}:admin"],
        )
        self._ws_publisher.publish(
            "team.birds_of_prey.self.updated",
            payload,
            channels=[f"channel:{game_id}:{team_id}"],
        )

    def _publish_enemy_visibility_snapshot(self, *, game_id: str, team_id: str, visible_enemy_eggs: list[Dict[str, Any]]) -> None:
        """Publish current enemy egg visibility snapshot for one team."""
        self._ws_publisher.publish(
            "team.birds_of_prey.enemy_eggs.visible",
            {
                "game_id": game_id,
                "team_id": team_id,
                "eggs": visible_enemy_eggs,
            },
            channels=[f"channel:{game_id}:{team_id}"],
        )

    def build_router(self) -> APIRouter:
        """Build and return Birds of Prey API routes."""
        router = APIRouter(prefix="/birds-of-prey", tags=["birds-of-prey"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            """Return bootstrap data for a team in Birds of Prey."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            state = self._service.get_team_bootstrap(db, game_id, team_id)
            state["config"] = self._repository.get_configuration(db, game_id)
            return TeamBootstrapResponse(state=state)

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            """Return admin overview data for Birds of Prey."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/config",
            response_model=BirdsOfPreyConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get birds of prey config",
        )
        def get_config(game_id: str, principal: CurrentPrincipal, db: DbSession) -> BirdsOfPreyConfigResponse:
            """Return persisted Birds of Prey configuration."""
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
            """Validate and persist Birds of Prey configuration updates."""
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
            """Drop an egg at the current team position and fan out WS updates."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            egg_id = str(body.egg_id or "").strip() or str(uuid4())

            try:
                result = self._service.drop_egg(db, game_id=game_id, team_id=team_id, egg_id=egg_id)
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

            active_eggs = self._service.get_active_eggs(db, game_id=game_id)
            dropped_egg = active_eggs.get(egg_id)
            if not isinstance(dropped_egg, dict):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="birds_of_prey.egg.persistFailed",
                )

            event_payload = self._build_egg_event_payload(game_id, dropped_egg)
            if not str(event_payload.get("owner_team_name") or "").strip():
                team = self._repository.get_team_by_game_and_id(db, game_id, team_id) or {}
                event_payload["owner_team_name"] = str(team.get("name") or "")

            self._ws_publisher.publish(
                "team.birds_of_prey.egg.added",
                event_payload,
                channels=[f"channel:{game_id}:{team_id}"],
            )
            self._ws_publisher.publish(
                "admin.birds_of_prey.egg.added",
                event_payload,
                channels=[f"channel:{game_id}:admin"],
            )

            teams = self._repository.fetch_teams_by_game_id(db, game_id)
            for team_row in teams:
                viewer_team_id = str(team_row.get("id") or "").strip()
                if not viewer_team_id:
                    continue
                visible_enemy_eggs = self._service.get_visible_enemy_eggs_for_team(db, game_id=game_id, team_id=viewer_team_id)
                self._publish_enemy_visibility_snapshot(game_id=game_id, team_id=viewer_team_id, visible_enemy_eggs=visible_enemy_eggs)
            
            return ActionResponse(
                success=result.success,
                message_key=self._localize_message_key(result.message_key, locale),
                action_id=result.action_id or None,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
            )

        @router.post("/{game_id}/teams/{team_id}/egg/destroy", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Destroy egg")
        def destroy_egg(game_id: str, team_id: str, body: DestroyEggRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            """Destroy an eligible enemy egg and publish resulting state updates."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.egg_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="birds_of_prey.validation.missingEggId")

            active_eggs_before = self._service.get_active_eggs(db, game_id=game_id)
            removed_egg = active_eggs_before.get(body.egg_id.strip())
            owner_team_id = str((removed_egg or {}).get("owner_team_id") or "").strip()

            try:
                result = self._service.destroy_egg(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    egg_id=body.egg_id.strip(),
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

            self._ws_publisher.publish(
                "team.birds_of_prey.egg.removed",
                {
                    "game_id": game_id,
                    "egg_id": body.egg_id.strip(),
                    "owner_team_id": owner_team_id,
                    "destroyed_by_team_id": team_id,
                },
                channels=[f"channel:{game_id}:{team_id}"],
            )
            if owner_team_id and owner_team_id != team_id:
                self._ws_publisher.publish(
                    "team.birds_of_prey.egg.removed",
                    {
                        "game_id": game_id,
                        "egg_id": body.egg_id.strip(),
                        "owner_team_id": owner_team_id,
                        "destroyed_by_team_id": team_id,
                    },
                    channels=[f"channel:{game_id}:{owner_team_id}"],
                )

            self._ws_publisher.publish(
                "admin.birds_of_prey.egg.removed",
                {
                    "game_id": game_id,
                    "egg_id": body.egg_id.strip(),
                    "owner_team_id": owner_team_id,
                    "destroyed_by_team_id": team_id,
                },
                channels=[f"channel:{game_id}:admin"],
            )

            attacker_team = self._repository.get_team_by_game_and_id(db, game_id, team_id) or {}
            attacker_score = int(attacker_team.get("geo_score") or 0)
            self._publish_team_score_event(game_id=game_id, team_id=team_id, score=attacker_score)

            visible_enemy_eggs = self._service.get_visible_enemy_eggs_for_team(db, game_id=game_id, team_id=team_id)
            self._publish_enemy_visibility_snapshot(game_id=game_id, team_id=team_id, visible_enemy_eggs=visible_enemy_eggs)
            if owner_team_id and owner_team_id != team_id:
                owner_visible = self._service.get_visible_enemy_eggs_for_team(db, game_id=game_id, team_id=owner_team_id)
                self._publish_enemy_visibility_snapshot(game_id=game_id, team_id=owner_team_id, visible_enemy_eggs=owner_visible)
            
            return ActionResponse(
                success=result.success,
                message_key=self._localize_message_key(result.message_key, locale),
                action_id=result.action_id or None,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
            )

        @router.post(
            "/{game_id}/teams/{team_id}/location/update",
            response_model=TeamLocationUpdateResponse,
            summary=f"{ACCESS_BOTH_LABEL} Update team location",
        )
        def update_location(
            game_id: str,
            team_id: str,
            body: TeamLocationUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> TeamLocationUpdateResponse:
            """Update team location and publish throttled location/visibility events."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)

            try:
                location = self._service.update_team_location(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    latitude=float(body.latitude),
                    longitude=float(body.longitude),
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

            visible_enemy_eggs = self._service.get_visible_enemy_eggs_for_team(db, game_id=game_id, team_id=team_id)
            should_publish = self._service.should_publish_location_event(db, game_id=game_id, team_id=team_id, min_interval_seconds=10)

            if should_publish:
                self._ws_publisher.publish(
                    "admin.birds_of_prey.team.location.updated",
                    {
                        "game_id": game_id,
                        "team_id": team_id,
                        "lat": location.get("lat"),
                        "lon": location.get("lon"),
                        "updated_at": location.get("updated_at"),
                    },
                    channels=[f"channel:{game_id}:admin"],
                )
                self._ws_publisher.publish(
                    "team.birds_of_prey.self.updated",
                    {
                        "game_id": game_id,
                        "team_id": team_id,
                        "location": location,
                    },
                    channels=[f"channel:{game_id}:{team_id}"],
                )
                self._publish_enemy_visibility_snapshot(game_id=game_id, team_id=team_id, visible_enemy_eggs=visible_enemy_eggs)

            return TeamLocationUpdateResponse(
                success=True,
                message_key=self._localize_message_key("birds_of_prey.location.updated", locale),
                location=location,
                visible_enemy_eggs=visible_enemy_eggs,
            )

        return router
