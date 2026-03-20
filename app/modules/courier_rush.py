from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.courier_rush_repository import CourierRushRepository
from app.services.courier_rush_service import CourierRushService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    overview: Dict[str, Any]


class PickupRequest(BaseModel):
    pickup_id: str = Field(min_length=1, max_length=64)


class DropoffRequest(BaseModel):
    dropoff_id: str = Field(min_length=1, max_length=64)
    points: int = Field(default=1, ge=0, le=1000)


class ActionResponse(BaseModel):
    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class CourierRushConfigResponse(BaseModel):
    config: Dict[str, Any]


class CourierRushConfigUpdateRequest(BaseModel):
    pickup_mode: str = Field(default="predefined", min_length=4, max_length=16)
    dropoff_mode: str = Field(default="random", min_length=4, max_length=16)
    max_active_pickups: int = Field(default=3, ge=1, le=25)
    pickup_spawn_area_geojson: Optional[str] = None


class PickupPayload(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    latitude: float
    longitude: float
    radius_meters: int = Field(default=25, ge=5, le=5000)
    points: int = Field(default=5, ge=1, le=100000)
    marker_color: str = Field(default="#2563eb", min_length=7, max_length=7)
    is_active: bool = True


class PickupResponse(BaseModel):
    pickup: Dict[str, Any]


class PickupListResponse(BaseModel):
    pickups: list[Dict[str, Any]]


class DropoffPayload(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    latitude: float
    longitude: float
    radius_meters: int = Field(default=25, ge=5, le=5000)
    marker_color: str = Field(default="#16a34a", min_length=7, max_length=7)
    is_active: bool = True


class DropoffResponse(BaseModel):
    dropoff: Dict[str, Any]


class DropoffListResponse(BaseModel):
    dropoffs: list[Dict[str, Any]]


class CourierRushModule(ApiModule, SharedModuleBase):
    name = "courier-rush"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        SharedModuleBase.__init__(self, game_type="courier_rush", ws_publisher=ws_publisher)
        self._service = CourierRushService()
        self._repository = CourierRushRepository()

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix="/courier-rush", tags=["courier-rush"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            state = self._service.get_team_bootstrap(db, game_id, team_id)
            state["pickups"] = [self._serialize_pickup(pickup) for pickup in self._repository.fetch_pickups_by_game_id(db, game_id)]
            state["dropoffs"] = [self._serialize_dropoff(dropoff) for dropoff in self._repository.fetch_dropoffs_by_game_id(db, game_id)]
            return TeamBootstrapResponse(state=state)

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/config",
            response_model=CourierRushConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get courier rush config",
        )
        def get_config(game_id: str, principal: CurrentPrincipal, db: DbSession) -> CourierRushConfigResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return CourierRushConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.put(
            "/{game_id}/config",
            response_model=CourierRushConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update courier rush config",
        )
        def update_config(
            game_id: str,
            body: CourierRushConfigUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> CourierRushConfigResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            pickup_mode = str(body.pickup_mode or "").strip().lower()
            dropoff_mode = str(body.dropoff_mode or "").strip().lower()
            if pickup_mode not in {"predefined", "random"}:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.config.invalidPickupMode")
            if dropoff_mode not in {"random", "fixed"}:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.config.invalidDropoffMode")

            spawn_area = self._normalize_spawn_area_geojson(body.pickup_spawn_area_geojson)
            if body.pickup_spawn_area_geojson and spawn_area is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.config.invalidSpawnArea")

            try:
                self._repository.update_configuration_without_commit(
                    db,
                    game_id,
                    {
                        "pickup_mode": pickup_mode,
                        "dropoff_mode": dropoff_mode,
                        "max_active_pickups": int(body.max_active_pickups),
                        "pickup_spawn_area_geojson": spawn_area,
                    },
                )
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.config.updateFailed") from error

            return CourierRushConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.get(
            "/{game_id}/pickups",
            response_model=PickupListResponse,
            summary=f"{ACCESS_ADMIN_LABEL} List courier rush pickups",
        )
        def list_pickups(game_id: str, principal: CurrentPrincipal, db: DbSession) -> PickupListResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            pickups = [self._serialize_pickup(pickup) for pickup in self._repository.fetch_pickups_by_game_id(db, game_id)]
            return PickupListResponse(pickups=pickups)

        @router.post(
            "/{game_id}/pickups",
            response_model=PickupResponse,
            status_code=status.HTTP_201_CREATED,
            summary=f"{ACCESS_ADMIN_LABEL} Create courier rush pickup",
        )
        def create_pickup(
            game_id: str,
            body: PickupPayload,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> PickupResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            values = self._validate_pickup_payload(body.model_dump())
            values["game_id"] = game_id

            try:
                pickup_id = self._repository.create_pickup_without_commit(db, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.pickup.createFailed") from error

            pickup = self._repository.get_pickup_by_game_id_and_pickup_id(db, game_id, pickup_id)
            if pickup is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="courier_rush.pickup.fetchFailed")
            return PickupResponse(pickup=self._serialize_pickup(pickup))

        @router.put(
            "/{game_id}/pickups/{pickup_id}",
            response_model=PickupResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update courier rush pickup",
        )
        def update_pickup(
            game_id: str,
            pickup_id: str,
            body: PickupPayload,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> PickupResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            existing = self._repository.get_pickup_by_game_id_and_pickup_id(db, game_id, pickup_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="courier_rush.pickup.notFound")

            values = self._validate_pickup_payload(body.model_dump())

            try:
                self._repository.update_pickup_without_commit(db, game_id, pickup_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.pickup.updateFailed") from error

            pickup = self._repository.get_pickup_by_game_id_and_pickup_id(db, game_id, pickup_id)
            if pickup is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="courier_rush.pickup.fetchFailed")
            return PickupResponse(pickup=self._serialize_pickup(pickup))

        @router.delete(
            "/{game_id}/pickups/{pickup_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            summary=f"{ACCESS_ADMIN_LABEL} Delete courier rush pickup",
        )
        def delete_pickup(game_id: str, pickup_id: str, principal: CurrentPrincipal, db: DbSession) -> None:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            existing = self._repository.get_pickup_by_game_id_and_pickup_id(db, game_id, pickup_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="courier_rush.pickup.notFound")

            try:
                self._repository.delete_pickup_without_commit(db, game_id, pickup_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.pickup.deleteFailed") from error

        @router.get(
            "/{game_id}/dropoffs",
            response_model=DropoffListResponse,
            summary=f"{ACCESS_ADMIN_LABEL} List courier rush dropoffs",
        )
        def list_dropoffs(game_id: str, principal: CurrentPrincipal, db: DbSession) -> DropoffListResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            dropoffs = [self._serialize_dropoff(dropoff) for dropoff in self._repository.fetch_dropoffs_by_game_id(db, game_id)]
            return DropoffListResponse(dropoffs=dropoffs)

        @router.post(
            "/{game_id}/dropoffs",
            response_model=DropoffResponse,
            status_code=status.HTTP_201_CREATED,
            summary=f"{ACCESS_ADMIN_LABEL} Create courier rush dropoff",
        )
        def create_dropoff(
            game_id: str,
            body: DropoffPayload,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> DropoffResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            values = self._validate_dropoff_payload(body.model_dump())
            values["game_id"] = game_id

            try:
                dropoff_id = self._repository.create_dropoff_without_commit(db, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.dropoff.createFailed") from error

            dropoff = self._repository.get_dropoff_by_game_id_and_dropoff_id(db, game_id, dropoff_id)
            if dropoff is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="courier_rush.dropoff.fetchFailed")
            return DropoffResponse(dropoff=self._serialize_dropoff(dropoff))

        @router.put(
            "/{game_id}/dropoffs/{dropoff_id}",
            response_model=DropoffResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update courier rush dropoff",
        )
        def update_dropoff(
            game_id: str,
            dropoff_id: str,
            body: DropoffPayload,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> DropoffResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            existing = self._repository.get_dropoff_by_game_id_and_dropoff_id(db, game_id, dropoff_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="courier_rush.dropoff.notFound")

            values = self._validate_dropoff_payload(body.model_dump())

            try:
                self._repository.update_dropoff_without_commit(db, game_id, dropoff_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.dropoff.updateFailed") from error

            dropoff = self._repository.get_dropoff_by_game_id_and_dropoff_id(db, game_id, dropoff_id)
            if dropoff is None:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="courier_rush.dropoff.fetchFailed")
            return DropoffResponse(dropoff=self._serialize_dropoff(dropoff))

        @router.delete(
            "/{game_id}/dropoffs/{dropoff_id}",
            status_code=status.HTTP_204_NO_CONTENT,
            summary=f"{ACCESS_ADMIN_LABEL} Delete courier rush dropoff",
        )
        def delete_dropoff(game_id: str, dropoff_id: str, principal: CurrentPrincipal, db: DbSession) -> None:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            existing = self._repository.get_dropoff_by_game_id_and_dropoff_id(db, game_id, dropoff_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="courier_rush.dropoff.notFound")

            try:
                self._repository.delete_dropoff_without_commit(db, game_id, dropoff_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.dropoff.deleteFailed") from error

        @router.post("/{game_id}/teams/{team_id}/pickup/confirm", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Confirm pickup")
        def confirm_pickup(game_id: str, team_id: str, body: PickupRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.pickup_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.validation.missingPickupId")

            result = self._service.confirm_pickup(db, game_id=game_id, team_id=team_id, pickup_id=body.pickup_id.strip())
            
            return ActionResponse(
                success=result.success,
                message_key=self._localize_message_key(result.message_key, locale),
                action_id=result.action_id or None,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
            )

        @router.post("/{game_id}/teams/{team_id}/dropoff/confirm", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Confirm dropoff")
        def confirm_dropoff(game_id: str, team_id: str, body: DropoffRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.dropoff_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.validation.missingDropoffId")

            result = self._service.confirm_dropoff(
                db,
                game_id=game_id,
                team_id=team_id,
                dropoff_id=body.dropoff_id.strip(),
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

    @staticmethod
    def _serialize_pickup(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(record.get("id") or ""),
            "title": str(record.get("title") or ""),
            "latitude": float(record.get("latitude") or 0),
            "longitude": float(record.get("longitude") or 0),
            "radius_meters": int(record.get("radius_meters") or 25),
            "points": int(record.get("points") or 1),
            "marker_color": str(record.get("marker_color") or "#2563eb"),
            "is_active": bool(record.get("is_active", True)),
        }

    @staticmethod
    def _serialize_dropoff(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(record.get("id") or ""),
            "title": str(record.get("title") or ""),
            "latitude": float(record.get("latitude") or 0),
            "longitude": float(record.get("longitude") or 0),
            "radius_meters": int(record.get("radius_meters") or 25),
            "marker_color": str(record.get("marker_color") or "#16a34a"),
            "is_active": bool(record.get("is_active", True)),
        }

    @staticmethod
    def _validate_pickup_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        marker_color = str(payload.get("marker_color") or "#2563eb").strip().lower()

        if not title:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.pickup.titleRequired")
        if len(marker_color) != 7 or not marker_color.startswith("#"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.pickup.invalidColor")

        return {
            "title": title,
            "latitude": float(payload.get("latitude") or 0),
            "longitude": float(payload.get("longitude") or 0),
            "radius_meters": max(5, int(payload.get("radius_meters") or 25)),
            "points": max(1, int(payload.get("points") or 1)),
            "marker_color": marker_color,
            "is_active": bool(payload.get("is_active", True)),
        }

    @staticmethod
    def _validate_dropoff_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        title = str(payload.get("title") or "").strip()
        marker_color = str(payload.get("marker_color") or "#16a34a").strip().lower()

        if not title:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.dropoff.titleRequired")
        if len(marker_color) != 7 or not marker_color.startswith("#"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="courier_rush.dropoff.invalidColor")

        return {
            "title": title,
            "latitude": float(payload.get("latitude") or 0),
            "longitude": float(payload.get("longitude") or 0),
            "radius_meters": max(5, int(payload.get("radius_meters") or 25)),
            "marker_color": marker_color,
            "is_active": bool(payload.get("is_active", True)),
        }

    @staticmethod
    def _normalize_spawn_area_geojson(raw: Optional[str]) -> Optional[str]:
        import json

        trimmed = str(raw or "").strip()
        if not trimmed:
            return None

        try:
            decoded = json.loads(trimmed)
        except Exception:
            return None

        if not isinstance(decoded, dict):
            return None

        geometry = decoded
        if decoded.get("type") == "Feature" and isinstance(decoded.get("geometry"), dict):
            geometry = decoded["geometry"]

        if geometry.get("type") != "Polygon" or not isinstance(geometry.get("coordinates"), list):
            return None

        return json.dumps({"type": "Polygon", "coordinates": geometry["coordinates"]}, separators=(",", ":"))
