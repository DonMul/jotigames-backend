from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.territory_control_repository import TerritoryControlRepository
from app.services.territory_control_service import TerritoryControlService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    overview: Dict[str, Any]


class ClaimZoneRequest(BaseModel):
    zone_id: str = Field(min_length=1, max_length=64)
    points: int = Field(default=1, ge=0, le=1000)


class ActionResponse(BaseModel):
    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class TerritoryZoneCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    latitude: float
    longitude: float
    radius_meters: int = Field(default=35, ge=10, le=10000)
    capture_points: int = Field(default=2, ge=1, le=1000)


class TerritoryZoneUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_meters: Optional[int] = Field(default=None, ge=10, le=10000)
    capture_points: Optional[int] = Field(default=None, ge=1, le=1000)


class TerritoryZoneRecordResponse(BaseModel):
    zone: Dict[str, Any]


class TerritoryZoneListResponse(BaseModel):
    zones: list[Dict[str, Any]]


class MessageResponse(BaseModel):
    message_key: str


class TerritoryControlModule(ApiModule, SharedModuleBase):
    name = "territory-control"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        """Initialize Territory Control module dependencies."""
        SharedModuleBase.__init__(self, game_type="territory_control", ws_publisher=ws_publisher)
        self._service = TerritoryControlService()
        self._repository = TerritoryControlRepository()

    @staticmethod
    def _serialize_zone(zone: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize territory zone row to API response structure."""
        return {
            "id": str(zone.get("id") or ""),
            "game_id": str(zone.get("game_id") or ""),
            "title": str(zone.get("title") or ""),
            "latitude": float(zone.get("latitude") or 0),
            "longitude": float(zone.get("longitude") or 0),
            "radius_meters": int(zone.get("radius_meters") or 35),
            "capture_points": int(zone.get("capture_points") or 2),
            "owner_team_id": str(zone.get("owner_team_id") or "") if zone.get("owner_team_id") else None,
            "captured_at": str(zone.get("captured_at") or "") if zone.get("captured_at") else None,
        }

    @staticmethod
    def _validate_zone_payload(*, latitude: float, longitude: float) -> None:
        """Validate zone coordinate bounds."""
        if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="territory_control.zone.invalidCoordinates")

    def build_router(self) -> APIRouter:
        """Build Territory Control routes for bootstrap, zones, and claims."""
        router = APIRouter(prefix="/territory-control", tags=["territory-control"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            """Return team-specific Territory Control bootstrap state."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            return TeamBootstrapResponse(state=self._service.get_team_bootstrap(db, game_id, team_id))

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            """Return admin overview data for Territory Control."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/zones",
            response_model=TerritoryZoneListResponse,
            summary=f"{ACCESS_ADMIN_LABEL} List zones",
        )
        def list_zones(game_id: str, principal: CurrentPrincipal, db: DbSession) -> TerritoryZoneListResponse:
            """List all zones configured for this game."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            zones = self._repository.fetch_zones_by_game_id(db, game_id)
            return TerritoryZoneListResponse(zones=[self._serialize_zone(zone) for zone in zones])

        @router.get(
            "/{game_id}/zones/{zone_id}",
            response_model=TerritoryZoneRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get zone",
        )
        def get_zone(game_id: str, zone_id: str, principal: CurrentPrincipal, db: DbSession) -> TerritoryZoneRecordResponse:
            """Return one zone record by identifier."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            zone = self._repository.get_zone_by_game_id_and_zone_id(db, game_id, zone_id)
            if zone is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="territory_control.zone.notFound")
            return TerritoryZoneRecordResponse(zone=self._serialize_zone(zone))

        @router.post(
            "/{game_id}/zones",
            response_model=TerritoryZoneRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Create zone",
        )
        def create_zone(
            game_id: str,
            body: TerritoryZoneCreateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> TerritoryZoneRecordResponse:
            """Create a new territory zone after validation."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            self._validate_zone_payload(latitude=body.latitude, longitude=body.longitude)

            zone_id = str(uuid4())
            values = {
                "id": zone_id,
                "game_id": game_id,
                "title": body.title.strip(),
                "latitude": body.latitude,
                "longitude": body.longitude,
                "radius_meters": int(body.radius_meters),
                "capture_points": int(body.capture_points),
            }

            try:
                self._repository.create_zone_without_commit(db, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="territory_control.zone.createFailed") from error

            created = self._repository.get_zone_by_game_id_and_zone_id(db, game_id, zone_id)
            if created is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="territory_control.zone.notFound")
            return TerritoryZoneRecordResponse(zone=self._serialize_zone(created))

        @router.put(
            "/{game_id}/zones/{zone_id}",
            response_model=TerritoryZoneRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update zone",
        )
        def update_zone(
            game_id: str,
            zone_id: str,
            body: TerritoryZoneUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> TerritoryZoneRecordResponse:
            """Update one territory zone after merged-state validation."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            current = self._repository.get_zone_by_game_id_and_zone_id(db, game_id, zone_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="territory_control.zone.notFound")

            next_lat = float(body.latitude if body.latitude is not None else current.get("latitude") or 0)
            next_lon = float(body.longitude if body.longitude is not None else current.get("longitude") or 0)
            self._validate_zone_payload(latitude=next_lat, longitude=next_lon)

            values: Dict[str, Any] = {}
            if body.title is not None:
                values["title"] = body.title.strip()
            if body.latitude is not None:
                values["latitude"] = body.latitude
            if body.longitude is not None:
                values["longitude"] = body.longitude
            if body.radius_meters is not None:
                values["radius_meters"] = int(body.radius_meters)
            if body.capture_points is not None:
                values["capture_points"] = int(body.capture_points)

            try:
                self._repository.update_zone_without_commit(db, game_id, zone_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="territory_control.zone.updateFailed") from error

            updated = self._repository.get_zone_by_game_id_and_zone_id(db, game_id, zone_id)
            if updated is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="territory_control.zone.notFound")
            return TerritoryZoneRecordResponse(zone=self._serialize_zone(updated))

        @router.delete(
            "/{game_id}/zones/{zone_id}",
            response_model=MessageResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Delete zone",
        )
        def delete_zone(game_id: str, zone_id: str, principal: CurrentPrincipal, db: DbSession) -> MessageResponse:
            """Delete a zone and return a confirmation message key."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            current = self._repository.get_zone_by_game_id_and_zone_id(db, game_id, zone_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="territory_control.zone.notFound")

            try:
                self._repository.delete_zone_without_commit(db, game_id, zone_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="territory_control.zone.deleteFailed") from error

            return MessageResponse(message_key="territory_control.zone.deleted")

        @router.post("/{game_id}/teams/{team_id}/zone/claim", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Claim zone")
        def claim_zone(game_id: str, team_id: str, body: ClaimZoneRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            """Record a zone-claim action by a team."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.zone_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="territory_control.validation.missingZoneId")

            result = self._service.claim_zone(
                db,
                game_id=game_id,
                team_id=team_id,
                zone_id=body.zone_id.strip(),
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
