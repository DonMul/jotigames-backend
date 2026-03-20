from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.pandemic_response_repository import PandemicResponseRepository
from app.services.pandemic_response_service import PandemicResponseService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    overview: Dict[str, Any]


class CollectPickupRequest(BaseModel):
    pickup_id: str = Field(min_length=1, max_length=64)


class ResolveHotspotRequest(BaseModel):
    hotspot_id: str = Field(min_length=1, max_length=64)
    points: int = Field(default=1, ge=0, le=1000)


class ActionResponse(BaseModel):
    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class PandemicResponseConfigResponse(BaseModel):
    config: Dict[str, Any]


class PandemicResponseConfigUpdateRequest(BaseModel):
    center_lat: float = Field(ge=-90, le=90)
    center_lon: float = Field(ge=-180, le=180)
    spawn_area_geojson: str = Field(min_length=1)
    severity_upgrade_seconds: int = Field(default=180, ge=30, le=86400)
    penalty_percent: int = Field(default=10, ge=1, le=90)
    target_active_hotspots: int = Field(default=15, ge=1, le=200)
    pickup_point_count: int = Field(default=4, ge=1, le=30)


class PandemicResponseStateResponse(BaseModel):
    hotspots: list[Dict[str, Any]]
    pickups: list[Dict[str, Any]]


class PandemicResponseModule(ApiModule, SharedModuleBase):
    name = "pandemic-response"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        SharedModuleBase.__init__(self, game_type="pandemic_response", ws_publisher=ws_publisher)
        self._service = PandemicResponseService()
        self._repository = PandemicResponseRepository()

    def build_router(self) -> APIRouter:
        router = APIRouter(prefix="/pandemic-response", tags=["pandemic-response"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            state = self._service.get_team_bootstrap(db, game_id, team_id)
            state["hotspots"] = [self._serialize_hotspot(record) for record in self._repository.fetch_hotspots_by_game_id(db, game_id)]
            state["pickups"] = [self._serialize_pickup(record) for record in self._repository.fetch_pickups_by_game_id(db, game_id)]
            return TeamBootstrapResponse(state=state)

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/config",
            response_model=PandemicResponseConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get pandemic response config",
        )
        def get_config(game_id: str, principal: CurrentPrincipal, db: DbSession) -> PandemicResponseConfigResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return PandemicResponseConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.put(
            "/{game_id}/config",
            response_model=PandemicResponseConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update pandemic response config",
        )
        def update_config(
            game_id: str,
            body: PandemicResponseConfigUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> PandemicResponseConfigResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            normalized_geojson = self._normalize_polygon_geojson(body.spawn_area_geojson)
            if normalized_geojson is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pandemic_response.config.invalidSpawnArea")

            values = {
                "center_lat": f"{float(body.center_lat):.7f}",
                "center_lon": f"{float(body.center_lon):.7f}",
                "spawn_area_geojson": normalized_geojson,
                "severity_upgrade_seconds": int(body.severity_upgrade_seconds),
                "penalty_percent": int(body.penalty_percent),
                "target_active_hotspots": int(body.target_active_hotspots),
                "pickup_point_count": int(body.pickup_point_count),
            }

            try:
                self._repository.update_configuration_without_commit(db, game_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pandemic_response.config.updateFailed") from error

            return PandemicResponseConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.get(
            "/{game_id}/admin/state",
            response_model=PandemicResponseStateResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Current pandemic admin state",
        )
        def get_admin_state(game_id: str, principal: CurrentPrincipal, db: DbSession) -> PandemicResponseStateResponse:
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            hotspots = [self._serialize_hotspot(record) for record in self._repository.fetch_hotspots_by_game_id(db, game_id)]
            pickups = [self._serialize_pickup(record) for record in self._repository.fetch_pickups_by_game_id(db, game_id)]
            return PandemicResponseStateResponse(hotspots=hotspots, pickups=pickups)

        @router.post("/{game_id}/teams/{team_id}/pickup/collect", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Collect pickup")
        def collect_pickup(game_id: str, team_id: str, body: CollectPickupRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.pickup_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pandemic_response.validation.missingPickupId")

            result = self._service.collect_pickup(db, game_id=game_id, team_id=team_id, pickup_id=body.pickup_id.strip())
            
            return ActionResponse(
                success=result.success,
                message_key=self._localize_message_key(result.message_key, locale),
                action_id=result.action_id or None,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
            )

        @router.post("/{game_id}/teams/{team_id}/hotspot/resolve", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Resolve hotspot")
        def resolve_hotspot(game_id: str, team_id: str, body: ResolveHotspotRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.hotspot_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pandemic_response.validation.missingHotspotId")

            result = self._service.resolve_hotspot(
                db,
                game_id=game_id,
                team_id=team_id,
                hotspot_id=body.hotspot_id.strip(),
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
    def _serialize_hotspot(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(record.get("id") or ""),
            "title": str(record.get("title") or ""),
            "latitude": float(record.get("latitude") or 0),
            "longitude": float(record.get("longitude") or 0),
            "radius_meters": int(record.get("radius_meters") or 25),
            "points": int(record.get("points") or 1),
            "severity_level": int(record.get("severity_level") or 1),
            "marker_color": str(record.get("marker_color") or "#dc2626"),
            "is_active": bool(record.get("is_active", True)),
        }

    @staticmethod
    def _serialize_pickup(record: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": str(record.get("id") or ""),
            "title": str(record.get("title") or ""),
            "resource_type": str(record.get("resource_type") or "first_aid"),
            "latitude": float(record.get("latitude") or 0),
            "longitude": float(record.get("longitude") or 0),
            "radius_meters": int(record.get("radius_meters") or 30),
            "marker_color": str(record.get("marker_color") or "#2563eb"),
            "is_active": bool(record.get("is_active", True)),
        }

    @staticmethod
    def _normalize_polygon_geojson(raw: str) -> str | None:
        import json

        text = str(raw or "").strip()
        if not text:
            return None

        try:
            decoded = json.loads(text)
        except Exception:
            return None

        if not isinstance(decoded, dict):
            return None

        if decoded.get("type") == "Feature" and isinstance(decoded.get("geometry"), dict):
            decoded = decoded["geometry"]

        if decoded.get("type") != "Polygon":
            return None

        rings = decoded.get("coordinates")
        if not isinstance(rings, list) or not rings or not isinstance(rings[0], list) or len(rings[0]) < 4:
            return None

        return json.dumps({"type": "Polygon", "coordinates": rings}, separators=(",", ":"))
