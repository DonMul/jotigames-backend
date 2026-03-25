from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.code_conspiracy_repository import CodeConspiracyRepository
from app.services.code_conspiracy_service import CodeConspiracyService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    """Response payload containing team bootstrap state for Code Conspiracy."""

    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    """Response payload containing admin overview state."""

    overview: Dict[str, Any]


class SubmitCodeRequest(BaseModel):
    """Request body for submitting a guess against another team."""

    target_team_id: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=64)
    points_delta: int = Field(default=0, ge=-1000, le=1000)


class ActionResponse(BaseModel):
    """Standardized action response with version and awarded points."""

    success: bool
    message_key: str
    action_id: Optional[str] = None
    points_awarded: int
    state_version: int


class CodeConspiracyConfigResponse(BaseModel):
    """Response wrapper around Code Conspiracy configuration."""

    config: Dict[str, Any]


class CodeConspiracyEndGameResponse(BaseModel):
    """Response returned after forcing game end."""

    success: bool


class CodeConspiracyConfigUpdateRequest(BaseModel):
    """Request body for updating Code Conspiracy tuning options."""

    code_length: int = Field(default=6, ge=4, le=10)
    character_set: str = Field(default="alphanumeric", min_length=3, max_length=24)
    submission_cooldown_seconds: int = Field(default=0, ge=0, le=300)
    correct_points: int = Field(default=10, ge=1, le=1000)
    penalty_enabled: bool = False
    penalty_value: int = Field(default=0, ge=0, le=1000)
    first_bonus_enabled: bool = False
    first_bonus_points: int = Field(default=0, ge=0, le=1000)
    win_condition_mode: str = Field(default="first_to_complete", min_length=3, max_length=32)


class CodeConspiracyModule(ApiModule, SharedModuleBase):
    """FastAPI routes for Code Conspiracy gameplay and admin workflows."""

    name = "code-conspiracy"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        """Initialize module services and repository dependencies."""
        SharedModuleBase.__init__(self, game_type="code_conspiracy", ws_publisher=ws_publisher)
        self._service = CodeConspiracyService()
        self._repository = CodeConspiracyRepository()

    def build_router(self) -> APIRouter:
        """Build and return the Code Conspiracy API router."""
        router = APIRouter(prefix="/code-conspiracy", tags=["code-conspiracy"])

        @router.get("/{game_id}/teams/{team_id}/bootstrap", response_model=TeamBootstrapResponse, summary=f"{ACCESS_BOTH_LABEL} Team bootstrap")
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            """Return team bootstrap data plus potential target teams."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            state = self._service.get_team_bootstrap(db, game_id, team_id)
            state["config"] = self._repository.get_configuration(db, game_id)
            state["target_teams"] = [
                {
                    "id": str(team.get("id") or ""),
                    "name": str(team.get("name") or ""),
                }
                for team in self._repository.fetch_teams_by_game_id(db, game_id)
                if str(team.get("id") or "") != team_id
            ]
            return TeamBootstrapResponse(state=state)

        @router.get("/{game_id}/overview", response_model=AdminOverviewResponse, summary=f"{ACCESS_ADMIN_LABEL} Admin overview")
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            """Return admin overview information for a game session."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/config",
            response_model=CodeConspiracyConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Get code conspiracy config",
        )
        def get_config(game_id: str, principal: CurrentPrincipal, db: DbSession) -> CodeConspiracyConfigResponse:
            """Return persisted Code Conspiracy configuration values."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return CodeConspiracyConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.put(
            "/{game_id}/config",
            response_model=CodeConspiracyConfigResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update code conspiracy config",
        )
        def update_config(
            game_id: str,
            body: CodeConspiracyConfigUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> CodeConspiracyConfigResponse:
            """Validate and persist Code Conspiracy configuration updates."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            character_set = str(body.character_set or "").strip().lower()
            if character_set not in {"alphanumeric", "letters", "numbers"}:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="code_conspiracy.config.invalidCharacterSet")

            win_condition_mode = str(body.win_condition_mode or "").strip().lower()
            if win_condition_mode not in {"first_to_complete", "highest_score_time_limit"}:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="code_conspiracy.config.invalidWinCondition")

            values = {
                "code_length": int(body.code_length),
                "character_set": character_set,
                "submission_cooldown_seconds": int(body.submission_cooldown_seconds),
                "correct_points": int(body.correct_points),
                "penalty_enabled": bool(body.penalty_enabled),
                "penalty_value": int(body.penalty_value),
                "first_bonus_enabled": bool(body.first_bonus_enabled),
                "first_bonus_points": int(body.first_bonus_points),
                "win_condition_mode": win_condition_mode,
            }

            try:
                self._repository.update_configuration_without_commit(db, game_id, values)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="code_conspiracy.config.updateFailed") from error

            return CodeConspiracyConfigResponse(config=self._repository.get_configuration(db, game_id))

        @router.post("/{game_id}/teams/{team_id}/code/submit", response_model=ActionResponse, summary=f"{ACCESS_BOTH_LABEL} Submit code")
        def submit_code(game_id: str, team_id: str, body: SubmitCodeRequest, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> ActionResponse:
            """Record a team-submitted code guess and return action metadata."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            if not body.target_team_id.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="code_conspiracy.validation.missingTargetTeamId")
            if not body.code.strip():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="code_conspiracy.validation.missingCode")

            result = self._service.submit_code(
                db,
                game_id=game_id,
                team_id=team_id,
                target_team_id=body.target_team_id.strip(),
                code_value=body.code.strip(),
                points_delta=body.points_delta,
            )
            
            return ActionResponse(
                success=result.success,
                message_key=self._localize_message_key(result.message_key, locale),
                action_id=result.action_id or None,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
            )

        @router.post(
            "/{game_id}/end",
            response_model=CodeConspiracyEndGameResponse,
            summary=f"{ACCESS_ADMIN_LABEL} End code conspiracy game",
        )
        def end_game(game_id: str, principal: CurrentPrincipal, db: DbSession) -> CodeConspiracyEndGameResponse:
            """Force end the game and persist the winning team snapshot."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            try:
                self._repository.end_game_without_commit(db, game_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="code_conspiracy.endGameFailed") from error

            return CodeConspiracyEndGameResponse(success=True)

        return router
