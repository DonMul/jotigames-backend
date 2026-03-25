from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.echo_hunt_repository import EchoHuntRepository
from app.services.echo_hunt_service import EchoHuntService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    """Response payload containing team bootstrap state."""

    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    """Response payload containing admin overview state."""

    overview: Dict[str, Any]


class ClaimBeaconRequest(BaseModel):
    """Request body for claiming a beacon."""

    beacon_id: str = Field(min_length=1, max_length=64)


class ActionResponse(BaseModel):
    """Standardized action response for team claims."""

    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class EchoHuntBeaconCreateRequest(BaseModel):
    """Request body for creating a beacon."""

    title: str = Field(min_length=1, max_length=120)
    hint: Optional[str] = Field(default=None, max_length=255)
    latitude: float
    longitude: float
    radius_meters: int = Field(default=25, ge=5, le=10000)
    signal_radius_meters: int = Field(default=0, ge=0, le=10000)
    points: int = Field(default=5, ge=1, le=10000)
    marker_color: str = Field(default="#7c3aed", min_length=7, max_length=7)
    is_active: bool = True


class EchoHuntBeaconUpdateRequest(BaseModel):
    """Request body for patching an existing beacon."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    hint: Optional[str] = Field(default=None, max_length=255)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_meters: Optional[int] = Field(default=None, ge=5, le=10000)
    signal_radius_meters: Optional[int] = Field(default=None, ge=0, le=10000)
    points: Optional[int] = Field(default=None, ge=1, le=10000)
    marker_color: Optional[str] = Field(default=None, min_length=7, max_length=7)
    is_active: Optional[bool] = None


class EchoHuntBeaconRecordResponse(BaseModel):
    """Response wrapper containing one beacon record."""

    beacon: Dict[str, Any]


class EchoHuntBeaconListResponse(BaseModel):
    """Response wrapper containing all beacon records."""

    beacons: list[Dict[str, Any]]


class MessageResponse(BaseModel):
    """Response wrapper for localized message keys."""

    message_key: str


class EchoHuntModule(ApiModule, SharedModuleBase):
    """FastAPI module for Echo Hunt admin and team routes."""

    name = "echo-hunt"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        """Initialize Echo Hunt module dependencies."""
        SharedModuleBase.__init__(self, game_type="echo_hunt", ws_publisher=ws_publisher)
        self._service = EchoHuntService()
        self._repository = EchoHuntRepository()

    @staticmethod
    def _serialize_beacon(beacon: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize beacon row to API response format."""
        return {
            "id": str(beacon.get("id") or ""),
            "game_id": str(beacon.get("game_id") or ""),
            "title": str(beacon.get("title") or ""),
            "hint": beacon.get("hint"),
            "latitude": float(beacon.get("latitude") or 0),
            "longitude": float(beacon.get("longitude") or 0),
            "radius_meters": int(beacon.get("radius_meters") or 25),
            "signal_radius_meters": int(beacon.get("signal_radius_meters") or 0),
            "points": int(beacon.get("points") or 0),
            "marker_color": str(beacon.get("marker_color") or "#7c3aed"),
            "is_active": bool(beacon.get("is_active")),
        }

    @staticmethod
    def _validate_beacon_payload(*, latitude: float, longitude: float, marker_color: str) -> None:
        """Validate beacon coordinates and marker color format."""
        if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="echo_hunt.beacon.invalidCoordinates")
        if len(marker_color) != 7 or not marker_color.startswith("#"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="echo_hunt.beacon.invalidColor")

    def build_router(self) -> APIRouter:
        """Build Echo Hunt routes for beacon admin and team claiming."""
        router = APIRouter(prefix="/echo-hunt", tags=["echo-hunt"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            """Return team-specific Echo Hunt bootstrap state."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            return TeamBootstrapResponse(state=self._service.get_team_bootstrap(db, game_id, team_id))

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            """Return admin overview data for Echo Hunt."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/beacons",
            response_model=EchoHuntBeaconListResponse,
            summary=f"{ACCESS_ADMIN_LABEL} List beacons",
        )
        def list_beacons(game_id: str, principal: CurrentPrincipal, db: DbSession) -> EchoHuntBeaconListResponse:
            """List all configured beacons for a game."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            beacons = self._repository.fetch_beacons_by_game_id(db, game_id)
            return EchoHuntBeaconListResponse(beacons=[self._serialize_beacon(beacon) for beacon in beacons])

        @router.get(
            "/{game_id}/beacons/{beacon_id}",
            response_model=EchoHuntBeaconRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get beacon",
        )
        def get_beacon(game_id: str, beacon_id: str, principal: CurrentPrincipal, db: DbSession) -> EchoHuntBeaconRecordResponse:
            """Fetch one beacon record by id."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            beacon = self._repository.get_beacon_by_game_id_and_beacon_id(db, game_id, beacon_id)
            if beacon is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="echo_hunt.beacon.notFound")
            return EchoHuntBeaconRecordResponse(beacon=self._serialize_beacon(beacon))

        @router.post(
            "/{game_id}/beacons",
            response_model=EchoHuntBeaconRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Create beacon",
        )
        def create_beacon(
            game_id: str,
            body: EchoHuntBeaconCreateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> EchoHuntBeaconRecordResponse:
            """Create a beacon after payload validation."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            self._validate_beacon_payload(latitude=body.latitude, longitude=body.longitude, marker_color=body.marker_color)

            beacon_id = str(uuid4())
            values = {
                "id": beacon_id,
                "game_id": game_id,
                "title": body.title.strip(),
                "hint": body.hint.strip() if body.hint else None,
                "latitude": body.latitude,
                "longitude": body.longitude,
                "radius_meters": int(body.radius_meters),
                "signal_radius_meters": int(body.signal_radius_meters),
                "points": int(body.points),
                "marker_color": body.marker_color.strip().lower(),
                "is_active": bool(body.is_active),
            }

            try:
                self._repository.create_beacon_without_commit(db, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="echo_hunt.beacon.createFailed") from error

            created = self._repository.get_beacon_by_game_id_and_beacon_id(db, game_id, beacon_id)
            if created is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="echo_hunt.beacon.notFound")
            return EchoHuntBeaconRecordResponse(beacon=self._serialize_beacon(created))

        @router.put(
            "/{game_id}/beacons/{beacon_id}",
            response_model=EchoHuntBeaconRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update beacon",
        )
        def update_beacon(
            game_id: str,
            beacon_id: str,
            body: EchoHuntBeaconUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> EchoHuntBeaconRecordResponse:
            """Update a beacon after merged-state validation."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            current = self._repository.get_beacon_by_game_id_and_beacon_id(db, game_id, beacon_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="echo_hunt.beacon.notFound")

            next_lat = float(body.latitude if body.latitude is not None else current.get("latitude") or 0)
            next_lon = float(body.longitude if body.longitude is not None else current.get("longitude") or 0)
            next_color = str(body.marker_color if body.marker_color is not None else current.get("marker_color") or "#7c3aed").strip().lower()
            self._validate_beacon_payload(latitude=next_lat, longitude=next_lon, marker_color=next_color)

            values: Dict[str, Any] = {}
            if body.title is not None:
                values["title"] = body.title.strip()
            if body.hint is not None:
                values["hint"] = body.hint.strip() or None
            if body.latitude is not None:
                values["latitude"] = body.latitude
            if body.longitude is not None:
                values["longitude"] = body.longitude
            if body.radius_meters is not None:
                values["radius_meters"] = int(body.radius_meters)
            if body.signal_radius_meters is not None:
                values["signal_radius_meters"] = int(body.signal_radius_meters)
            if body.points is not None:
                values["points"] = int(body.points)
            if body.marker_color is not None:
                values["marker_color"] = body.marker_color.strip().lower()
            if body.is_active is not None:
                values["is_active"] = bool(body.is_active)

            try:
                self._repository.update_beacon_without_commit(db, game_id, beacon_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="echo_hunt.beacon.updateFailed") from error

            updated = self._repository.get_beacon_by_game_id_and_beacon_id(db, game_id, beacon_id)
            if updated is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="echo_hunt.beacon.notFound")
            return EchoHuntBeaconRecordResponse(beacon=self._serialize_beacon(updated))

        @router.delete(
            "/{game_id}/beacons/{beacon_id}",
            response_model=MessageResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Delete beacon",
        )
        def delete_beacon(game_id: str, beacon_id: str, principal: CurrentPrincipal, db: DbSession) -> MessageResponse:
            """Delete a beacon and return localized confirmation key."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            current = self._repository.get_beacon_by_game_id_and_beacon_id(db, game_id, beacon_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="echo_hunt.beacon.notFound")

            try:
                self._repository.delete_beacon_without_commit(db, game_id, beacon_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="echo_hunt.beacon.deleteFailed") from error

            return MessageResponse(message_key="echo_hunt.beacon.deleted")

        @router.post("/{game_id}/teams/{team_id}/beacon/claim", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Claim beacon")
        def claim_beacon(game_id: str, team_id: str, body: ClaimBeaconRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            """Record beacon claim action for the requesting team."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.beacon_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="echo_hunt.validation.missingBeaconId")

            result = self._service.claim_beacon(
                db,
                game_id=game_id,
                team_id=team_id,
                beacon_id=body.beacon_id.strip(),
            )
            
            return ActionResponse(
                success=result.success,
                message_key=self._localize_message_key(result.message_key, locale),
                action_id=result.action_id or None,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
            )

        return router
