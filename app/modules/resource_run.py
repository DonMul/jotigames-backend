from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.resource_run_repository import ResourceRunRepository
from app.services.resource_run_service import ResourceRunService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    overview: Dict[str, Any]


class ClaimResourceRequest(BaseModel):
    node_id: str = Field(min_length=1, max_length=64)
    points: int = Field(default=1, ge=0, le=1000)


class ActionResponse(BaseModel):
    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class ResourceRunNodeCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    resource_type: str = Field(min_length=1, max_length=32)
    points: int = Field(default=1, ge=1, le=1000)
    latitude: float
    longitude: float
    radius_meters: int = Field(default=25, ge=5, le=10000)
    marker_color: str = Field(default="#ef4444", min_length=7, max_length=7)


class ResourceRunNodeUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    resource_type: Optional[str] = Field(default=None, min_length=1, max_length=32)
    points: Optional[int] = Field(default=None, ge=1, le=1000)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_meters: Optional[int] = Field(default=None, ge=5, le=10000)
    marker_color: Optional[str] = Field(default=None, min_length=7, max_length=7)


class ResourceRunNodeRecordResponse(BaseModel):
    node: Dict[str, Any]


class ResourceRunNodeListResponse(BaseModel):
    nodes: list[Dict[str, Any]]


class MessageResponse(BaseModel):
    message_key: str


class ResourceRunModule(ApiModule, SharedModuleBase):
    name = "resource-run"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        """Initialize Resource Run module dependencies."""
        SharedModuleBase.__init__(self, game_type="resource_run", ws_publisher=ws_publisher)
        self._service = ResourceRunService()
        self._repository = ResourceRunRepository()

    @staticmethod
    def _serialize_node(node: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize resource node row to API response payload."""
        return {
            "id": str(node.get("id") or ""),
            "game_id": str(node.get("game_id") or ""),
            "title": str(node.get("title") or ""),
            "resource_type": str(node.get("resource_type") or ""),
            "points": int(node.get("points") or 0),
            "latitude": float(node.get("latitude") or 0),
            "longitude": float(node.get("longitude") or 0),
            "radius_meters": int(node.get("radius_meters") or 25),
            "marker_color": str(node.get("marker_color") or "#ef4444"),
        }

    @staticmethod
    def _validate_node_payload(*, latitude: float, longitude: float, marker_color: str) -> None:
        """Validate node coordinates and hex marker color format."""
        if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resource_run.node.invalidCoordinates")
        if len(marker_color) != 7 or not marker_color.startswith("#"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resource_run.node.invalidColor")

    def build_router(self) -> APIRouter:
        """Build Resource Run admin/team routes for nodes and claims."""
        router = APIRouter(prefix="/resource-run", tags=["resource-run"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            """Return team-specific Resource Run bootstrap state."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            return TeamBootstrapResponse(state=self._service.get_team_bootstrap(db, game_id, team_id))

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            """Return admin overview data for Resource Run."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/nodes",
            response_model=ResourceRunNodeListResponse,
            summary=f"{ACCESS_ADMIN_LABEL} List nodes",
        )
        def list_nodes(game_id: str, principal: CurrentPrincipal, db: DbSession) -> ResourceRunNodeListResponse:
            """List all configured resource nodes for this game."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            nodes = self._repository.fetch_nodes_by_game_id(db, game_id)
            return ResourceRunNodeListResponse(nodes=[self._serialize_node(node) for node in nodes])

        @router.get(
            "/{game_id}/nodes/{node_id}",
            response_model=ResourceRunNodeRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get node",
        )
        def get_node(game_id: str, node_id: str, principal: CurrentPrincipal, db: DbSession) -> ResourceRunNodeRecordResponse:
            """Return one resource node by identifier."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            node = self._repository.get_node_by_game_id_and_node_id(db, game_id, node_id)
            if node is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resource_run.node.notFound")
            return ResourceRunNodeRecordResponse(node=self._serialize_node(node))

        @router.post(
            "/{game_id}/nodes",
            response_model=ResourceRunNodeRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Create node",
        )
        def create_node(
            game_id: str,
            body: ResourceRunNodeCreateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> ResourceRunNodeRecordResponse:
            """Create a new resource node after validation."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            self._validate_node_payload(latitude=body.latitude, longitude=body.longitude, marker_color=body.marker_color)

            node_id = str(uuid4())
            values = {
                "id": node_id,
                "game_id": game_id,
                "title": body.title.strip(),
                "resource_type": body.resource_type.strip(),
                "points": int(body.points),
                "latitude": body.latitude,
                "longitude": body.longitude,
                "radius_meters": int(body.radius_meters),
                "marker_color": body.marker_color.strip().lower(),
            }

            try:
                self._repository.create_node_without_commit(db, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resource_run.node.createFailed") from error

            created = self._repository.get_node_by_game_id_and_node_id(db, game_id, node_id)
            if created is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resource_run.node.notFound")
            return ResourceRunNodeRecordResponse(node=self._serialize_node(created))

        @router.put(
            "/{game_id}/nodes/{node_id}",
            response_model=ResourceRunNodeRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update node",
        )
        def update_node(
            game_id: str,
            node_id: str,
            body: ResourceRunNodeUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> ResourceRunNodeRecordResponse:
            """Update one resource node after merged-state validation."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            current = self._repository.get_node_by_game_id_and_node_id(db, game_id, node_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resource_run.node.notFound")

            next_lat = float(body.latitude if body.latitude is not None else current.get("latitude") or 0)
            next_lon = float(body.longitude if body.longitude is not None else current.get("longitude") or 0)
            next_color = str(body.marker_color if body.marker_color is not None else current.get("marker_color") or "#ef4444").strip().lower()
            self._validate_node_payload(latitude=next_lat, longitude=next_lon, marker_color=next_color)

            values: Dict[str, Any] = {}
            if body.title is not None:
                values["title"] = body.title.strip()
            if body.resource_type is not None:
                values["resource_type"] = body.resource_type.strip()
            if body.points is not None:
                values["points"] = int(body.points)
            if body.latitude is not None:
                values["latitude"] = body.latitude
            if body.longitude is not None:
                values["longitude"] = body.longitude
            if body.radius_meters is not None:
                values["radius_meters"] = int(body.radius_meters)
            if body.marker_color is not None:
                values["marker_color"] = body.marker_color.strip().lower()

            try:
                self._repository.update_node_without_commit(db, game_id, node_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resource_run.node.updateFailed") from error

            updated = self._repository.get_node_by_game_id_and_node_id(db, game_id, node_id)
            if updated is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resource_run.node.notFound")
            return ResourceRunNodeRecordResponse(node=self._serialize_node(updated))

        @router.delete(
            "/{game_id}/nodes/{node_id}",
            response_model=MessageResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Delete node",
        )
        def delete_node(game_id: str, node_id: str, principal: CurrentPrincipal, db: DbSession) -> MessageResponse:
            """Delete a resource node and return confirmation key."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            current = self._repository.get_node_by_game_id_and_node_id(db, game_id, node_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="resource_run.node.notFound")

            try:
                self._repository.delete_node_without_commit(db, game_id, node_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resource_run.node.deleteFailed") from error

            return MessageResponse(message_key="resource_run.node.deleted")

        @router.post("/{game_id}/teams/{team_id}/resource/claim", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Claim resource")
        def claim_resource(game_id: str, team_id: str, body: ClaimResourceRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            """Record a resource claim action by a team."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.node_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="resource_run.validation.missingNodeId")

            result = self._service.claim_resource(
                db,
                game_id=game_id,
                team_id=team_id,
                node_id=body.node_id.strip(),
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
