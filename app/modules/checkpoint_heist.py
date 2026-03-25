from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.checkpoint_heist_repository import CheckpointHeistRepository
from app.services.checkpoint_heist_service import CheckpointHeistService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    """Response payload containing team bootstrap state."""

    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    """Response payload containing admin overview state."""

    overview: Dict[str, Any]


class CaptureCheckpointRequest(BaseModel):
    """Request body for confirming a checkpoint capture."""

    checkpoint_id: str = Field(min_length=1, max_length=64)
    points: int = Field(default=1, ge=0, le=1000)


class ActionResponse(BaseModel):
    """Standardized action response for capture events."""

    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class CheckpointCreateRequest(BaseModel):
    """Request payload for creating a checkpoint."""

    title: str = Field(min_length=1, max_length=120)
    latitude: float
    longitude: float
    radius_meters: int = Field(default=25, ge=5, le=10000)
    points: int = Field(default=5, ge=1, le=10000)
    marker_color: str = Field(default="#dc2626", min_length=7, max_length=7)
    is_active: bool = True


class CheckpointUpdateRequest(BaseModel):
    """Request payload for patching an existing checkpoint."""

    title: Optional[str] = Field(default=None, min_length=1, max_length=120)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    radius_meters: Optional[int] = Field(default=None, ge=5, le=10000)
    points: Optional[int] = Field(default=None, ge=1, le=10000)
    marker_color: Optional[str] = Field(default=None, min_length=7, max_length=7)
    is_active: Optional[bool] = None


class CheckpointReorderRequest(BaseModel):
    """Request payload defining new checkpoint order."""

    ordered_ids: list[str] = Field(default_factory=list)


class CheckpointRecordResponse(BaseModel):
    """Response wrapper containing one checkpoint record."""

    checkpoint: Dict[str, Any]


class CheckpointListResponse(BaseModel):
    """Response wrapper containing all checkpoint records."""

    checkpoints: list[Dict[str, Any]]


class MessageResponse(BaseModel):
    """Response wrapper for localized message keys."""

    message_key: str


class CheckpointHeistModule(ApiModule, SharedModuleBase):
    """FastAPI module for Checkpoint Heist configuration and captures."""

    name = "checkpoint-heist"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        """Initialize Checkpoint Heist module dependencies."""
        SharedModuleBase.__init__(self, game_type="checkpoint_heist", ws_publisher=ws_publisher)
        self._service = CheckpointHeistService()
        self._repository = CheckpointHeistRepository()

    @staticmethod
    def _serialize_checkpoint(checkpoint: Dict[str, Any]) -> Dict[str, Any]:
        """Serialize checkpoint row to stable API response shape."""
        order_index = checkpoint.get("order_index")
        if order_index is None:
            order_index = checkpoint.get("sequence_order")
        return {
            "id": str(checkpoint.get("id") or ""),
            "game_id": str(checkpoint.get("game_id") or ""),
            "title": str(checkpoint.get("title") or ""),
            "order_index": int(order_index or 0),
            "latitude": float(checkpoint.get("latitude") or 0),
            "longitude": float(checkpoint.get("longitude") or 0),
            "radius_meters": int(checkpoint.get("radius_meters") or 25),
            "points": int(checkpoint.get("points") or 0),
            "marker_color": str(checkpoint.get("marker_color") or "#dc2626"),
            "is_active": bool(checkpoint.get("is_active")),
        }

    @staticmethod
    def _validate_checkpoint_payload(*, latitude: float, longitude: float, marker_color: str) -> None:
        """Validate checkpoint coordinates and marker color format."""
        if latitude < -90 or latitude > 90 or longitude < -180 or longitude > 180:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="checkpoint_heist.checkpoint.invalidCoordinates")
        if len(marker_color) != 7 or not marker_color.startswith("#"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="checkpoint_heist.checkpoint.invalidColor")

    def build_router(self) -> APIRouter:
        """Build Checkpoint Heist routes for admin config and capture actions."""
        router = APIRouter(prefix="/checkpoint-heist", tags=["checkpoint-heist"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            """Return team-specific Checkpoint Heist bootstrap state."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            return TeamBootstrapResponse(state=self._service.get_team_bootstrap(db, game_id, team_id))

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            """Return admin overview data for Checkpoint Heist."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/checkpoints",
            response_model=CheckpointListResponse,
            summary=f"{ACCESS_ADMIN_LABEL} List checkpoints",
        )
        def list_checkpoints(game_id: str, principal: CurrentPrincipal, db: DbSession) -> CheckpointListResponse:
            """List all checkpoints configured for this game."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            checkpoints = self._repository.fetch_checkpoints_by_game_id(db, game_id)
            return CheckpointListResponse(checkpoints=[self._serialize_checkpoint(checkpoint) for checkpoint in checkpoints])

        @router.post(
            "/{game_id}/checkpoints",
            response_model=CheckpointRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Create checkpoint",
        )
        def create_checkpoint(
            game_id: str,
            body: CheckpointCreateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> CheckpointRecordResponse:
            """Create a checkpoint and return the persisted record."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            self._validate_checkpoint_payload(latitude=body.latitude, longitude=body.longitude, marker_color=body.marker_color)

            checkpoint_id = str(uuid4())
            order_column_name = self._repository.get_order_column_name(db)
            values = {
                "id": checkpoint_id,
                "game_id": game_id,
                "title": body.title.strip(),
                "latitude": body.latitude,
                "longitude": body.longitude,
                "radius_meters": int(body.radius_meters),
                "points": int(body.points),
                "marker_color": body.marker_color.strip().lower(),
                "is_active": bool(body.is_active),
            }
            values[order_column_name] = self._repository.get_next_order_index(db, game_id)

            try:
                self._repository.create_checkpoint_without_commit(db, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="checkpoint_heist.checkpoint.createFailed") from error

            created = self._repository.get_checkpoint_by_game_id_and_checkpoint_id(db, game_id, checkpoint_id)
            if created is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="checkpoint_heist.checkpoint.notFound")
            return CheckpointRecordResponse(checkpoint=self._serialize_checkpoint(created))

        @router.put(
            "/{game_id}/checkpoints/{checkpoint_id}",
            response_model=CheckpointRecordResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update checkpoint",
        )
        def update_checkpoint(
            game_id: str,
            checkpoint_id: str,
            body: CheckpointUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> CheckpointRecordResponse:
            """Update one checkpoint and return its latest persisted state."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            current = self._repository.get_checkpoint_by_game_id_and_checkpoint_id(db, game_id, checkpoint_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="checkpoint_heist.checkpoint.notFound")

            next_lat = float(body.latitude if body.latitude is not None else current.get("latitude") or 0)
            next_lon = float(body.longitude if body.longitude is not None else current.get("longitude") or 0)
            next_color = str(body.marker_color if body.marker_color is not None else current.get("marker_color") or "#dc2626").strip().lower()
            self._validate_checkpoint_payload(latitude=next_lat, longitude=next_lon, marker_color=next_color)

            values: Dict[str, Any] = {}
            if body.title is not None:
                values["title"] = body.title.strip()
            if body.latitude is not None:
                values["latitude"] = body.latitude
            if body.longitude is not None:
                values["longitude"] = body.longitude
            if body.radius_meters is not None:
                values["radius_meters"] = int(body.radius_meters)
            if body.points is not None:
                values["points"] = int(body.points)
            if body.marker_color is not None:
                values["marker_color"] = body.marker_color.strip().lower()
            if body.is_active is not None:
                values["is_active"] = bool(body.is_active)

            try:
                self._repository.update_checkpoint_without_commit(db, game_id, checkpoint_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="checkpoint_heist.checkpoint.updateFailed") from error

            updated = self._repository.get_checkpoint_by_game_id_and_checkpoint_id(db, game_id, checkpoint_id)
            if updated is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="checkpoint_heist.checkpoint.notFound")
            return CheckpointRecordResponse(checkpoint=self._serialize_checkpoint(updated))

        @router.delete(
            "/{game_id}/checkpoints/{checkpoint_id}",
            response_model=MessageResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Delete checkpoint",
        )
        def delete_checkpoint(game_id: str, checkpoint_id: str, principal: CurrentPrincipal, db: DbSession) -> MessageResponse:
            """Delete a checkpoint and return confirmation message key."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            current = self._repository.get_checkpoint_by_game_id_and_checkpoint_id(db, game_id, checkpoint_id)
            if current is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="checkpoint_heist.checkpoint.notFound")

            try:
                self._repository.delete_checkpoint_without_commit(db, game_id, checkpoint_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="checkpoint_heist.checkpoint.deleteFailed") from error

            return MessageResponse(message_key="checkpoint_heist.checkpoint.deleted")

        @router.post(
            "/{game_id}/checkpoints/reorder",
            response_model=CheckpointListResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Reorder checkpoints",
        )
        def reorder_checkpoints(
            game_id: str,
            body: CheckpointReorderRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> CheckpointListResponse:
            """Reorder checkpoints based on ordered id list."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            try:
                self._repository.reorder_checkpoints_without_commit(db, game_id, body.ordered_ids)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="checkpoint_heist.checkpoint.reorderFailed") from error

            checkpoints = self._repository.fetch_checkpoints_by_game_id(db, game_id)
            return CheckpointListResponse(checkpoints=[self._serialize_checkpoint(checkpoint) for checkpoint in checkpoints])

        @router.post("/{game_id}/teams/{team_id}/capture/confirm", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Capture checkpoint")
        def capture_checkpoint(game_id: str, team_id: str, body: CaptureCheckpointRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            """Record checkpoint capture action for a team."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.checkpoint_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="checkpoint_heist.validation.missingCheckpointId")

            result = self._service.capture_checkpoint(
                db,
                game_id=game_id,
                team_id=team_id,
                checkpoint_id=body.checkpoint_id.strip(),
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
