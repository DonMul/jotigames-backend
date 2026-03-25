from datetime import UTC, datetime
from random import randint
from typing import Any, Dict, Optional
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.game_chat_repository import GameChatRepository
from app.repositories.game_repository import GameRepository
from app.repositories.team_repository import TeamRepository
from app.repositories.user_repository import UserRepository
from app.services.team_logo_catalog import TeamLogoCatalogService
from app.services.game_initializer import GameInitializerService
from app.services.game_reset import GameResetService
from app.services.ws_client import WsEventPublisher


class GameTypesResponse(BaseModel):
    game_types: list[str]


class GameTypeAvailabilityRecord(BaseModel):
    game_type: str
    enabled: bool


class GameTypeAvailabilityResponse(BaseModel):
    game_types: list[GameTypeAvailabilityRecord]


class GameTypeAvailabilityUpdateRequest(BaseModel):
    enabled_game_types: list[str] = Field(default_factory=list)


class GameRecordResponse(BaseModel):
    game: Dict[str, Any]


class GameSummaryResponse(BaseModel):
    id: str
    name: str
    type: str
    start_at: str
    end_at: str


class GamesListResponse(BaseModel):
    games: list[GameSummaryResponse]


class GameMemberResponse(BaseModel):
    user_id: str
    email: str
    roles: list[str]


class GameMembersResponse(BaseModel):
    members: list[GameMemberResponse]


class TeamRecordResponse(BaseModel):
    team: Dict[str, Any]


class TeamsListResponse(BaseModel):
    teams: list[Dict[str, Any]]


class TeamLogoCategoryResponse(BaseModel):
    key: str
    label: str


class TeamLogoOptionResponse(BaseModel):
    value: str
    label: str
    category: str


class TeamLogoCatalogResponse(BaseModel):
    categories: list[TeamLogoCategoryResponse]
    options: list[TeamLogoOptionResponse]


class TeamDashboardBootstrapResponse(BaseModel):
    game_id: str
    team_id: str
    game_type: str
    game_name: str
    game_code: str
    team_name: str
    team_logo_path: Optional[str] = None
    lives: int
    teams: list[Dict[str, Any]] = Field(default_factory=list)


class GameCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    start_at: datetime
    end_at: datetime
    game_type: str = Field(min_length=1, max_length=64)


class GameUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    code: Optional[str] = Field(default=None, min_length=1, max_length=64)
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    game_type: Optional[str] = Field(default=None, min_length=1, max_length=64)
    settings: Dict[str, Any] = Field(default_factory=dict)


class GameMemberAssignRequest(BaseModel):
    email: EmailStr


class TeamCreateRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    logo_path: Optional[str] = Field(default=None, max_length=255)


class TeamUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=120)
    logo_path: Optional[str] = Field(default=None, max_length=255)


class SendTeamMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    level: str = Field(default="info", min_length=1, max_length=32)


class TeamMessageResponse(BaseModel):
    message_key: str
    message_id: str


class GameChatMessageRecord(BaseModel):
    id: str
    game_id: str
    message: str
    sent_at: str
    author_role: str
    author_label: str
    author_team_id: Optional[str] = None
    author_logo_path: Optional[str] = None
    author_session_id: Optional[str] = None


class GameChatHistoryResponse(BaseModel):
    game_id: str
    messages: list[GameChatMessageRecord]


class GameChatSendRequest(BaseModel):
    message: str = Field(min_length=1, max_length=512)


class GameChatSendResponse(BaseModel):
    game_id: str
    message: GameChatMessageRecord


class GameModule(ApiModule, SharedModuleBase):
    name = "game"
    _DEFAULT_TEAM_LIVES = 9

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        """Initialize game domain module dependencies and realtime publisher."""
        SharedModuleBase.__init__(self, game_type="game", ws_publisher=ws_publisher, game_type_detail_key="game")
        self._ws_publisher = ws_publisher
        self._gameRepository = GameRepository()
        self._gameChatRepository = GameChatRepository()
        self._teamRepository = TeamRepository()
        self._teamLogoCatalogService = TeamLogoCatalogService()
        self._userRepository = UserRepository()
        self._gameInitializerService = GameInitializerService()
        self._gameResetService = GameResetService()

    @staticmethod
    def _apply_settings_payload(
        *,
        values: Dict[str, Any],
        settings: Dict[str, Any],
        immutable_columns: set[str],
    ) -> None:
        """Merge arbitrary settings fields into values while protecting immutable columns."""
        for key, value in settings.items():
            if key in immutable_columns:
                continue
            values[key] = value

    @staticmethod
    def _validate_required_values(values: Dict[str, Any]) -> None:
        """Validate required game fields and chronological time ordering."""
        for required in ["name", "code", "start_at", "end_at", "game_type"]:
            if required not in values or values[required] is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.validation.requiredFieldsMissing",
                )

        if values["start_at"] >= values["end_at"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="game.validation.startBeforeEndRequired",
            )

    def _require_existing_game(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        """Load a game by id or raise standardized not-found HTTP error."""
        game = self._gameRepository.getGameById(db, game_id)
        if game is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game.notFound")
        return game

    def _generate_unique_game_code(self, db: DbSession) -> str:
        """Generate a collision-free six-digit game code with bounded retries."""
        attempts = 100
        for _ in range(attempts):
            code = f"{randint(0, 999999):06d}"
            if not self._gameRepository.hasGameCode(db, code):
                return code

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="game.validation.codeGenerationFailed",
        )

    def _require_can_manage_game(self, db: DbSession, game_id: str, principal: CurrentPrincipal) -> Dict[str, Any]:
        """Authorize owner/admin management access for a game and return the game.

        Platform admins (ROLE_ADMIN / ROLE_SUPER_ADMIN) bypass per-game
        ownership checks and can manage any game.
        """
        game = self._require_existing_game(db, game_id)
        if principal.is_admin:
            return game
        is_owner = self._gameRepository.isGameOwnerByGameIdAndUserId(db, game_id, principal.principal_id)
        is_admin = self._gameRepository.hasGameManagerByGameIdAndUserId(db, game_id, principal.principal_id)
        if is_owner or is_admin:
            return game

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="game.auth.manageAccessRequired",
        )

    def _require_can_view_game(self, db: DbSession, game_id: str, principal: CurrentPrincipal) -> Dict[str, Any]:
        """Authorize owner/admin/game-master view access and return the game.

        Platform admins (ROLE_ADMIN / ROLE_SUPER_ADMIN) bypass per-game
        ownership checks and can view any game.
        """
        game = self._require_existing_game(db, game_id)
        if principal.is_admin:
            return game
        is_owner = self._gameRepository.isGameOwnerByGameIdAndUserId(db, game_id, principal.principal_id)
        is_admin = self._gameRepository.hasGameManagerByGameIdAndUserId(db, game_id, principal.principal_id)
        is_game_master = self._gameRepository.hasGameMasterByGameIdAndUserId(db, game_id, principal.principal_id)
        if is_owner or is_admin or is_game_master:
            return game

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="game.auth.viewAccessRequired",
        )

    def _require_team_in_game(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Load team in game scope or raise not-found HTTP error."""
        team = self._teamRepository.getTeamByGameIdAndTeamId(db, game_id, team_id)
        if team is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team.notFound")
        return team

    def _require_can_view_team(self, db: DbSession, game_id: str, team_id: str, principal: CurrentPrincipal) -> Dict[str, Any]:
        """Authorize team self-view or user game-view access for team records."""
        team = self._require_team_in_game(db, game_id, team_id)
        if principal.principal_type == "team":
            if principal.principal_id != team_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="team.auth.selfRequired")
            return team

        self._ensure_user_principal(principal)
        self._require_can_view_game(db, game_id, principal)
        return team

    def _require_can_update_team(self, db: DbSession, game_id: str, team_id: str, principal: CurrentPrincipal) -> Dict[str, Any]:
        """Authorize team self-update or admin manage access for team updates."""
        team = self._require_team_in_game(db, game_id, team_id)
        if principal.principal_type == "team":
            if principal.principal_id != team_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="team.auth.selfRequired")
            return team

        self._ensure_user_principal(principal)
        self._require_can_manage_game(db, game_id, principal)
        return team

    def _require_can_access_game_chat(self, db: DbSession, game_id: str, principal: CurrentPrincipal) -> None:
        """Authorize chat access for teams in-game and users with manage rights.

        Platform admins bypass per-game ownership checks.
        """
        if principal.principal_type == "team":
            team = self._teamRepository.getTeamByGameIdAndTeamId(db, game_id, principal.principal_id)
            if team is None:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="team.auth.selfRequired")
            return

        self._ensure_user_principal(principal)
        if principal.is_admin:
            return
        self._require_can_manage_game(db, game_id, principal)

    @staticmethod
    def _team_realtime_payload(team: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Normalize team record to minimal realtime payload contract."""
        team_data = team if isinstance(team, dict) else {}
        return {
            "team_id": str(team_data.get("id") or ""),
            "team_name": str(team_data.get("name") or ""),
            "team_logo": str(team_data.get("logo_path") or ""),
            "lives": int(team_data.get("lives") or 0),
        }

    def _publish_team_updated_events(self, *, game_id: str, team: Dict[str, Any]) -> None:
        """Publish team update deltas to admin, game-wide, and team-private channels."""
        payload = self._team_realtime_payload(team)
        team_id = str(payload.get("team_id") or "").strip()
        if not team_id:
            return

        self._ws_publisher.publish(
            "admin.general.team.update",
            payload,
            channels=[f"channel:{game_id}:admin"],
        )
        self._ws_publisher.publish(
            "game.general.team.update",
            payload,
            channels=[f"channel:{game_id}"],
        )
        self._ws_publisher.publish(
            "team.general.team.update",
            payload,
            channels=[f"channel:{game_id}:{team_id}"],
        )

    def _publish_team_added_events(self, *, game_id: str, team: Dict[str, Any]) -> None:
        """Publish team-added events to admin and game-wide channels."""
        payload = self._team_realtime_payload(team)
        team_id = str(payload.get("team_id") or "").strip()
        if not team_id:
            return

        self._ws_publisher.publish(
            "admin.general.team.add",
            payload,
            channels=[f"channel:{game_id}:admin"],
        )
        self._ws_publisher.publish(
            "game.general.team.add",
            payload,
            channels=[f"channel:{game_id}"],
        )

    def _publish_team_removed_events(self, *, game_id: str, team: Dict[str, Any]) -> None:
        """Publish team-removed events to admin and game-wide channels."""
        payload = self._team_realtime_payload(team)
        team_id = str(payload.get("team_id") or "").strip()
        if not team_id:
            return

        self._ws_publisher.publish(
            "admin.general.team.remove",
            payload,
            channels=[f"channel:{game_id}:admin"],
        )
        self._ws_publisher.publish(
            "game.general.team.remove",
            payload,
            channels=[f"channel:{game_id}"],
        )

    def build_router(self) -> APIRouter:
        """Build all game-domain routes for admin/team management and chat."""
        router = APIRouter(prefix="/game", tags=["game"])

        @router.get("/game-types", response_model=GameTypesResponse, summary="List available game types")
        def get_game_types(db: DbSession) -> GameTypesResponse:
            """Return globally enabled game types for game creation UX."""
            enabled_types = self._gameRepository.fetchGameTypesByEnabled(db, True)
            return GameTypesResponse(game_types=enabled_types)

        @router.get(
            "/game-types/availability",
            response_model=GameTypeAvailabilityResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Super admin game type availability",
        )
        def get_game_type_availability(principal: CurrentPrincipal, db: DbSession) -> GameTypeAvailabilityResponse:
            """Return full game-type availability matrix for admin controls."""
            self._ensure_user_principal(principal)
            if not principal.is_admin:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auth.user.superAdminRequired")

            records = self._gameRepository.fetchGameTypeAvailability(db)
            records.sort(key=lambda record: str(record.get("game_type") or ""))
            return GameTypeAvailabilityResponse(
                game_types=[
                    GameTypeAvailabilityRecord(
                        game_type=str(record.get("game_type") or ""),
                        enabled=bool(record.get("enabled")),
                    )
                    for record in records
                ]
            )

        @router.put(
            "/game-types/availability",
            response_model=GameTypeAvailabilityResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Super admin update game type availability",
        )
        def update_game_type_availability(
            body: GameTypeAvailabilityUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> GameTypeAvailabilityResponse:
            """Replace global game-type availability states from normalized request list."""
            self._ensure_user_principal(principal)
            if not principal.is_admin:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="auth.user.superAdminRequired")

            normalized: list[str] = []
            seen: set[str] = set()
            for value in body.enabled_game_types:
                game_type = str(value or "").strip().lower()
                if not game_type or game_type in seen:
                    continue
                seen.add(game_type)
                normalized.append(game_type)

            try:
                self._gameRepository.replaceGameTypeAvailabilityWithoutCommit(db, normalized)
                self._gameRepository.commitChanges(db)
            except Exception as error:
                self._gameRepository.rollbackOnError(db, error)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.gameTypes.updateFailed",
                ) from error

            records = self._gameRepository.fetchGameTypeAvailability(db)
            records.sort(key=lambda record: str(record.get("game_type") or ""))
            return GameTypeAvailabilityResponse(
                game_types=[
                    GameTypeAvailabilityRecord(
                        game_type=str(record.get("game_type") or ""),
                        enabled=bool(record.get("enabled")),
                    )
                    for record in records
                ]
            )

        @router.get("", response_model=GamesListResponse, summary=f"{ACCESS_ADMIN_LABEL} List games")
        def list_games(principal: CurrentPrincipal, db: DbSession) -> GamesListResponse:
            """List unique games accessible to current user.

            Platform admins (ROLE_ADMIN / ROLE_SUPER_ADMIN) see ALL games.
            Regular users see only games they own, manage, or game-master.
            """
            self._ensure_user_principal(principal)

            if principal.is_admin:
                rows = self._gameRepository.fetchAllGameSummaries(db)
            else:
                rows = self._gameRepository.fetchGameSummariesByOwnerId(db, principal.principal_id)
                rows.extend(self._gameRepository.fetchGameSummariesByManagerUserId(db, principal.principal_id))
                rows.extend(self._gameRepository.fetchGameSummariesByGameMasterUserId(db, principal.principal_id))

            unique_rows: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                unique_rows[str(row.get("id"))] = row

            games: list[GameSummaryResponse] = []
            for row in unique_rows.values():
                games.append(
                    GameSummaryResponse(
                        id=str(self._serialize_value(row.get("id"))),
                        name=str(self._serialize_value(row.get("name"))),
                        type=str(self._serialize_value(row.get("game_type"))),
                        start_at=str(self._serialize_value(row.get("start_at"))),
                        end_at=str(self._serialize_value(row.get("end_at"))),
                    )
                )
            return GamesListResponse(games=games)

        @router.get("/team-logos", response_model=TeamLogoCatalogResponse, summary=f"{ACCESS_BOTH_LABEL} List team logo options")
        def list_team_logo_options(principal: CurrentPrincipal) -> TeamLogoCatalogResponse:
            """Return catalog of allowed team logos for both team and admin clients."""
            if principal.principal_type not in {"team", "user"}:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="team.auth.selfRequired")

            categories, options = self._teamLogoCatalogService.listCatalog()

            return TeamLogoCatalogResponse(
                categories=[TeamLogoCategoryResponse(key=key, label=label) for key, label in categories],
                options=[
                    TeamLogoOptionResponse(
                        value=option["value"],
                        label=option["label"],
                        category=option["category"],
                    )
                    for option in options
                ],
            )

        @router.get("/{game_id}", response_model=GameRecordResponse, summary=f"{ACCESS_ADMIN_LABEL} Get game")
        def get_game(game_id: str, principal: CurrentPrincipal, db: DbSession) -> GameRecordResponse:
            """Return serialized game record when caller has game view permissions."""
            self._ensure_user_principal(principal)

            row = self._require_can_view_game(db, game_id, principal)
            return GameRecordResponse(game=self._serialize_row(row))

        @router.get("/{game_id}/members", response_model=GameMembersResponse, summary=f"{ACCESS_ADMIN_LABEL} List game members")
        def get_game_members(game_id: str, principal: CurrentPrincipal, db: DbSession) -> GameMembersResponse:
            """Return deduplicated list of owner/admin/game-master members for a game."""
            self._ensure_user_principal(principal)
            game = self._require_can_view_game(db, game_id, principal)

            members_by_user_id: Dict[str, set[str]] = {}

            owner_id = str(game.get("owner_id"))
            if owner_id:
                members_by_user_id.setdefault(owner_id, set()).add("owner")

            for admin_user_id in self._gameRepository.fetchGameManagerUserIdsByGameId(db, game_id):
                members_by_user_id.setdefault(admin_user_id, set()).add("admin")

            for game_master_user_id in self._gameRepository.fetchGameMasterUserIdsByGameId(db, game_id):
                members_by_user_id.setdefault(game_master_user_id, set()).add("game_master")

            members: list[GameMemberResponse] = []
            for user_id, roles in members_by_user_id.items():
                email = self._userRepository.getUserEmailById(db, user_id) or user_id
                members.append(GameMemberResponse(user_id=user_id, email=email, roles=sorted(list(roles))))

            members.sort(key=lambda member: member.user_id)
            return GameMembersResponse(members=members)

        @router.post("", response_model=GameRecordResponse, summary=f"{ACCESS_ADMIN_LABEL} Create game")
        def create_game(body: GameCreateRequest, principal: CurrentPrincipal, db: DbSession) -> GameRecordResponse:
            """Create game, initialize type-specific state, and return created record."""
            self._ensure_user_principal(principal)

            values: Dict[str, Any] = {
                "name": body.name,
                "code": self._generate_unique_game_code(db),
                "start_at": self._to_db_datetime(body.start_at),
                "end_at": self._to_db_datetime(body.end_at),
                "game_type": body.game_type,
            }

            if "id" not in values:
                values["id"] = str(uuid4())
            if "admin_token" not in values:
                values["admin_token"] = str(uuid4())
            if "owner_id" not in values:
                values["owner_id"] = principal.principal_id

            required_check = {
                "name": values.get("name"),
                "code": values.get("code"),
                "start_at": values.get("start_at"),
                "end_at": values.get("end_at"),
                "game_type": values.get("game_type"),
            }
            self._validate_required_values(required_check)

            try:
                self._gameRepository.createGameByValuesWithoutCommit(db, values)
                self._gameInitializerService.initializeGameByIdAndType(
                    db,
                    str(values["id"]),
                    str(values.get("game_type", "")),
                )
                self._gameRepository.commitChanges(db)
            except Exception as error:
                self._gameRepository.rollbackOnError(db, error)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.create.failed",
                ) from error

            created = self._gameRepository.getGameById(db, str(values["id"]))
            if created is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game.notFound")

            return GameRecordResponse(game=self._serialize_row(created))

        @router.put("/{game_id}", response_model=GameRecordResponse, summary=f"{ACCESS_ADMIN_LABEL} Update game")
        def update_game(
            game_id: str,
            body: GameUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> GameRecordResponse:
            """Update mutable game fields/settings after manage-access validation."""
            self._ensure_user_principal(principal)

            current = self._require_can_manage_game(db, game_id, principal)

            values: Dict[str, Any] = {}
            if body.name is not None:
                values["name"] = body.name
            if body.code is not None:
                values["code"] = body.code
            if body.start_at is not None:
                values["start_at"] = self._to_db_datetime(body.start_at)
            if body.end_at is not None:
                values["end_at"] = self._to_db_datetime(body.end_at)
            if body.game_type is not None:
                values["game_type"] = body.game_type

            self._apply_settings_payload(
                values=values,
                settings=body.settings,
                immutable_columns={"id"},
            )

            merged = dict(current)
            merged.update(values)
            required_check = {
                "name": merged.get("name"),
                "code": merged.get("code"),
                "start_at": merged.get("start_at"),
                "end_at": merged.get("end_at"),
                "game_type": merged.get("game_type"),
            }
            self._validate_required_values(required_check)

            try:
                self._gameRepository.updateGameById(db, game_id, values)
            except Exception as error:
                self._gameRepository.rollbackOnError(db, error)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.update.failed",
                ) from error

            updated = self._gameRepository.getGameById(db, game_id)
            if updated is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game.notFound")

            return GameRecordResponse(game=self._serialize_row(updated))

        @router.delete("/{game_id}", response_model=MessageKeyResponse, summary=f"{ACCESS_ADMIN_LABEL} Delete game")
        def delete_game(game_id: str, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> MessageKeyResponse:
            """Delete a game and dependent domain state, returning localized success key."""
            self._ensure_user_principal(principal)

            self._require_can_manage_game(db, game_id, principal)

            try:
                self._gameRepository.deleteGameById(db, game_id)
            except Exception as error:
                self._gameRepository.rollbackOnError(db, error)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.delete.failed",
                ) from error

            return MessageKeyResponse(message_key=self._localize_message_key("game.delete.success", locale))

        @router.post("/{game_id}/reset", response_model=MessageKeyResponse, summary=f"{ACCESS_ADMIN_LABEL} Reset game")
        def reset_game(game_id: str, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> MessageKeyResponse:
            """Reset game runtime state using game-type specific reset service."""
            self._ensure_user_principal(principal)
            game = self._require_can_manage_game(db, game_id, principal)

            game_type = str(game.get("game_type") or "")
            try:
                self._gameResetService.resetGameByIdAndType(db, game_id, game_type)
            except Exception as error:
                self._gameRepository.rollbackOnError(db, error)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.reset.failed",
                ) from error

            return MessageKeyResponse(message_key=self._localize_message_key("game.reset.success", locale))

        @router.get("/{game_id}/teams", response_model=TeamsListResponse, summary=f"{ACCESS_ADMIN_LABEL} List teams")
        def list_teams(game_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamsListResponse:
            """List all teams in a game for authorized user principals."""
            self._ensure_user_principal(principal)
            self._require_can_view_game(db, game_id, principal)

            rows = self._teamRepository.fetchTeamsByGameId(db, game_id)
            return TeamsListResponse(teams=[self._serialize_row(row) for row in rows])

        @router.get("/team/dashboard", response_model=TeamDashboardBootstrapResponse, summary="Team dashboard bootstrap")
        def get_team_dashboard(principal: CurrentPrincipal, db: DbSession) -> TeamDashboardBootstrapResponse:
            """Bootstrap team dashboard with game/team metadata and peer team list."""
            if principal.principal_type != "team":
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="team.auth.selfRequired")

            team = self._teamRepository.getTeamById(db, principal.principal_id)
            if team is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team.notFound")

            game_id = str(team.get("game_id") or "")
            if game_id == "":
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game.notFound")

            game = self._gameRepository.getGameById(db, game_id)
            if game is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game.notFound")

            return TeamDashboardBootstrapResponse(
                game_id=game_id,
                team_id=str(team.get("id") or ""),
                game_type=str(game.get("game_type") or ""),
                game_name=str(game.get("name") or ""),
                game_code=str(game.get("code") or ""),
                team_name=str(team.get("name") or ""),
                team_logo_path=str(team.get("logo_path") or "") or None,
                lives=int(team.get("lives") or 0),
                teams=[self._serialize_row(row) for row in self._teamRepository.fetchTeamsByGameId(db, game_id)],
            )

        @router.get("/{game_id}/teams/{team_id}", response_model=TeamRecordResponse, summary=f"{ACCESS_BOTH_LABEL} Get team")
        def get_team(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamRecordResponse:
            """Return a single team record with cross-role access validation."""
            team = self._require_can_view_team(db, game_id, team_id, principal)
            return TeamRecordResponse(team=self._serialize_row(team))

        @router.post("/{game_id}/teams", response_model=TeamRecordResponse, summary=f"{ACCESS_ADMIN_LABEL} Create team")
        def create_team(
            game_id: str,
            body: TeamCreateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> TeamRecordResponse:
            """Create a new team in the game and publish realtime team-added events."""
            self._ensure_user_principal(principal)
            self._require_can_manage_game(db, game_id, principal)

            try:
                code = self._teamRepository.generateUniqueTeamCodeByGameId(db, game_id)
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

            values: Dict[str, Any] = {
                "id": str(uuid4()),
                "game_id": game_id,
                "name": body.name,
                "code": code,
                "lives": self._DEFAULT_TEAM_LIVES,
            }
            if body.logo_path is not None:
                values["logo_path"] = body.logo_path

            try:
                self._teamRepository.createTeamByValues(db, values)
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error
            except Exception as error:
                self._gameRepository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="team.create.failed") from error

            created = self._teamRepository.getTeamByGameIdAndTeamId(db, game_id, values["id"])
            if created is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team.notFound")

            self._publish_team_added_events(game_id=game_id, team=created)
            return TeamRecordResponse(team=self._serialize_row(created))

        @router.put("/{game_id}/teams/{team_id}", response_model=TeamRecordResponse, summary=f"{ACCESS_BOTH_LABEL} Update team")
        def update_team(
            game_id: str,
            team_id: str,
            body: TeamUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> TeamRecordResponse:
            """Update team profile fields and publish realtime team-updated events."""
            self._require_can_update_team(db, game_id, team_id, principal)

            values: Dict[str, Any] = {}
            if body.name is not None:
                values["name"] = body.name
            if body.logo_path is not None:
                values["logo_path"] = body.logo_path

            if not values:
                unchanged = self._teamRepository.getTeamByGameIdAndTeamId(db, game_id, team_id)
                if unchanged is None:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team.notFound")
                return TeamRecordResponse(team=self._serialize_row(unchanged))

            try:
                self._teamRepository.updateTeamByGameIdAndTeamId(db, game_id, team_id, values)
            except Exception as error:
                self._gameRepository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="team.update.failed") from error

            updated = self._teamRepository.getTeamByGameIdAndTeamId(db, game_id, team_id)
            if updated is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team.notFound")

            self._publish_team_updated_events(game_id=game_id, team=updated)
            return TeamRecordResponse(team=self._serialize_row(updated))

        @router.delete("/{game_id}/teams/{team_id}", response_model=MessageKeyResponse, summary=f"{ACCESS_ADMIN_LABEL} Delete team")
        def delete_team(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession, locale: CurrentLocale) -> MessageKeyResponse:
            """Delete team from a game and emit realtime team-removed notifications."""
            self._ensure_user_principal(principal)
            self._require_can_manage_game(db, game_id, principal)
            team = self._require_team_in_game(db, game_id, team_id)

            try:
                self._teamRepository.deleteTeamByGameIdAndTeamId(db, game_id, team_id)
            except Exception as error:
                self._gameRepository.rollbackOnError(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="team.delete.failed") from error

            self._publish_team_removed_events(game_id=game_id, team=team)

            return MessageKeyResponse(message_key=self._localize_message_key("team.delete.success", locale))

        @router.post("/{game_id}/teams/{team_id}/message", response_model=TeamMessageResponse, summary=f"{ACCESS_ADMIN_LABEL} Send message to team")
        def send_team_message(
            game_id: str,
            team_id: str,
            body: SendTeamMessageRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> TeamMessageResponse:
            """Persist admin-to-team message and publish targeted team WS event."""
            self._ensure_user_principal(principal)
            self._require_can_manage_game(db, game_id, principal)
            self._require_team_in_game(db, game_id, team_id)

            message_id = str(uuid4())
            message_text = body.message.strip()
            level = body.level.strip().lower()
            created_at = datetime.now(UTC).isoformat()

            if level == "":
                level = "info"

            try:
                self._gameChatRepository.createTeamMessageWithoutCommit(
                    db,
                    message_id=message_id,
                    game_id=game_id,
                    team_id=team_id,
                    created_by_id=principal.principal_id,
                    message=message_text,
                )
                self._gameChatRepository.commitChanges(db)
            except Exception as error:
                self._gameChatRepository.rollbackOnError(db, error)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.teamMessage.sendFailed",
                ) from error

            self._ws_publisher.publish(
                "team.general.message",
                {
                    "teamId": team_id,
                    "id": message_id,
                    "message": message_text,
                    "title": "Message from admin",
                    "level": level,
                    "from": "admin",
                    "gameId": game_id,
                    "createdAt": created_at,
                },
                channels=[f"channel:{game_id}:{team_id}"],
            )

            return TeamMessageResponse(
                message_key=self._localize_message_key("game.teamMessage.sent", locale),
                message_id=message_id,
            )

        @router.get("/{game_id}/chat", response_model=GameChatHistoryResponse, summary=f"{ACCESS_BOTH_LABEL} Get game chat history")
        def get_game_chat_history(
            game_id: str,
            principal: CurrentPrincipal,
            db: DbSession,
            limit: int = 50,
        ) -> GameChatHistoryResponse:
            """Return bounded game chat history for authorized principals."""
            self._require_can_access_game_chat(db, game_id, principal)

            safe_limit = max(1, min(limit, 256))
            rows = self._gameChatRepository.fetchGameChatMessagesByGameId(db, game_id, limit=safe_limit)

            messages: list[GameChatMessageRecord] = []
            for row in rows:
                messages.append(
                    GameChatMessageRecord(
                        id=str(row.get("id") or ""),
                        game_id=str(row.get("game_id") or ""),
                        message=str(row.get("message") or ""),
                        sent_at=str(self._serialize_value(row.get("created_at"))),
                        author_role=str(row.get("author_role") or ""),
                        author_label=str(row.get("author_label") or ""),
                        author_team_id=str(row.get("author_team_id")) if row.get("author_team_id") else None,
                        author_logo_path=str(row.get("author_logo_path")) if row.get("author_logo_path") else None,
                        author_session_id=str(row.get("author_session_id")) if row.get("author_session_id") else None,
                    )
                )

            return GameChatHistoryResponse(game_id=game_id, messages=messages)

        @router.post("/{game_id}/chat", response_model=GameChatSendResponse, summary=f"{ACCESS_BOTH_LABEL} Send game chat message")
        def send_game_chat_message(
            game_id: str,
            body: GameChatSendRequest,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> GameChatSendResponse:
            """Create chat message record, fan out realtime event, and return payload."""
            self._require_can_access_game_chat(db, game_id, principal)

            message_text = body.message.strip()
            if message_text == "":
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="game.chat.emptyMessage")

            author_role = "admin"
            author_label = "Admin"
            author_team_id: Optional[str] = None
            author_logo_path: Optional[str] = None

            if principal.principal_type == "team":
                team = self._teamRepository.getTeamByGameIdAndTeamId(db, game_id, principal.principal_id)
                if team is None:
                    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="team.auth.selfRequired")
                author_role = "team"
                author_label = str(team.get("name") or "Team")
                author_team_id = str(team.get("id") or "")
                author_logo_path = str(team.get("logo_path")) if team.get("logo_path") else None
            else:
                display_name = self._userRepository.getUserDisplayNameById(db, principal.principal_id)
                if display_name:
                    author_label = display_name

            message_id = str(uuid4())
            session_marker = f"api:{principal.principal_type}:{principal.principal_id}"
            created_at_db = datetime.now(UTC).replace(tzinfo=None)
            created_at_iso = created_at_db.replace(tzinfo=UTC).isoformat()

            values = {
                "id": message_id,
                "game_id": game_id,
                "message": message_text,
                "author_role": author_role,
                "author_label": author_label,
                "author_team_id": author_team_id,
                "author_logo_path": author_logo_path,
                "author_user_id": principal.principal_id if principal.principal_type == "user" else None,
                "author_session_id": session_marker,
                "created_at": created_at_db,
            }

            try:
                self._gameChatRepository.createGameChatMessageWithoutCommit(db, values)
                self._gameChatRepository.commitChanges(db)
            except Exception as error:
                self._gameChatRepository.rollbackOnError(db, error)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.chat.sendFailed",
                ) from error

            payload = {
                "id": message_id,
                "gameId": game_id,
                "message": message_text,
                "sentAt": created_at_iso,
                "authorRole": author_role,
                "authorLabel": author_label,
                "authorTeamId": author_team_id,
                "authorLogoPath": author_logo_path,
                "authorSessionId": session_marker,
            }

            self._ws_publisher.publish("game.chat.message", payload)

            return GameChatSendResponse(
                game_id=game_id,
                message=GameChatMessageRecord(
                    id=message_id,
                    game_id=game_id,
                    message=message_text,
                    sent_at=created_at_iso,
                    author_role=author_role,
                    author_label=author_label,
                    author_team_id=author_team_id,
                    author_logo_path=author_logo_path,
                    author_session_id=session_marker,
                ),
            )

        @router.post("/{game_id}/admins", response_model=MessageKeyResponse, summary=f"{ACCESS_ADMIN_LABEL} Add game admin")
        def add_game_admin(
            game_id: str,
            body: GameMemberAssignRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> MessageKeyResponse:
            """Assign admin role to a game member, removing game-master role if needed."""
            self._ensure_user_principal(principal)
            self._require_can_manage_game(db, game_id, principal)

            assigned_user_id = self._userRepository.getUserIdByEmail(db, str(body.email))
            if assigned_user_id is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game.member.emailNotFound")

            game = self._require_can_manage_game(db, game_id, principal)
            owner_id = str(game.get("owner_id") or "")
            if assigned_user_id == owner_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.member.roleConflict.ownerOnly",
                )

            has_admin_role = self._gameRepository.hasGameManagerByGameIdAndUserId(db, game_id, assigned_user_id)
            has_game_master_role = self._gameRepository.hasGameMasterByGameIdAndUserId(db, game_id, assigned_user_id)

            if has_game_master_role:
                try:
                    self._gameRepository.deleteGameMasterByGameIdAndUserId(db, game_id, assigned_user_id)
                except Exception as error:
                    self._gameRepository.rollbackOnError(db, error)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="game.gameMaster.remove.failed",
                    ) from error

            if not has_admin_role:
                try:
                    self._gameRepository.createGameManagerByGameIdAndUserId(db, game_id, assigned_user_id)
                except Exception as error:
                    self._gameRepository.rollbackOnError(db, error)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="game.admin.add.failed",
                    ) from error

            return MessageKeyResponse(message_key=self._localize_message_key("game.admin.add.success", locale))

        @router.delete("/{game_id}/admins/{user_id}", response_model=MessageKeyResponse, summary=f"{ACCESS_ADMIN_LABEL} Remove game admin")
        def remove_game_admin(
            game_id: str,
            user_id: str,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> MessageKeyResponse:
            """Remove admin role with safeguards for owner and self-removal."""
            self._ensure_user_principal(principal)
            game = self._require_can_manage_game(db, game_id, principal)

            owner_id = str(game.get("owner_id"))
            if user_id == owner_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.admin.remove.ownerForbidden",
                )

            if user_id == principal.principal_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.admin.remove.selfForbidden",
                )

            try:
                self._gameRepository.deleteGameManagerByGameIdAndUserId(db, game_id, user_id)
            except Exception as error:
                self._gameRepository.rollbackOnError(db, error)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.admin.remove.failed",
                ) from error

            return MessageKeyResponse(message_key=self._localize_message_key("game.admin.remove.success", locale))

        @router.post("/{game_id}/game-masters", response_model=MessageKeyResponse, summary=f"{ACCESS_ADMIN_LABEL} Add game master")
        def add_game_master(
            game_id: str,
            body: GameMemberAssignRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> MessageKeyResponse:
            """Assign game-master role, replacing admin role when both conflict."""
            self._ensure_user_principal(principal)
            self._require_can_manage_game(db, game_id, principal)

            assigned_user_id = self._userRepository.getUserIdByEmail(db, str(body.email))
            if assigned_user_id is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game.member.emailNotFound")

            game = self._require_can_manage_game(db, game_id, principal)
            owner_id = str(game.get("owner_id") or "")
            if assigned_user_id == owner_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.member.roleConflict.ownerOnly",
                )

            has_admin_role = self._gameRepository.hasGameManagerByGameIdAndUserId(db, game_id, assigned_user_id)
            has_game_master_role = self._gameRepository.hasGameMasterByGameIdAndUserId(db, game_id, assigned_user_id)

            if has_admin_role:
                try:
                    self._gameRepository.deleteGameManagerByGameIdAndUserId(db, game_id, assigned_user_id)
                except Exception as error:
                    self._gameRepository.rollbackOnError(db, error)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="game.admin.remove.failed",
                    ) from error

            if not has_game_master_role:
                try:
                    self._gameRepository.createGameMasterByGameIdAndUserId(db, game_id, assigned_user_id)
                except Exception as error:
                    self._gameRepository.rollbackOnError(db, error)
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="game.gameMaster.add.failed",
                    ) from error

            return MessageKeyResponse(message_key=self._localize_message_key("game.gameMaster.add.success", locale))

        @router.delete("/{game_id}/game-masters/{user_id}", response_model=MessageKeyResponse, summary=f"{ACCESS_ADMIN_LABEL} Remove game master")
        def remove_game_master(
            game_id: str,
            user_id: str,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> MessageKeyResponse:
            """Remove game-master role assignment from a game member."""
            self._ensure_user_principal(principal)
            self._require_can_manage_game(db, game_id, principal)

            try:
                self._gameRepository.deleteGameMasterByGameIdAndUserId(db, game_id, user_id)
            except Exception as error:
                self._gameRepository.rollbackOnError(db, error)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="game.gameMaster.remove.failed",
                ) from error

            return MessageKeyResponse(message_key=self._localize_message_key("game.gameMaster.remove.success", locale))

        return router


class MessageKeyResponse(BaseModel):
    message_key: str
