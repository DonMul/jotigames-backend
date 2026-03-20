from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Dict

from fastapi import HTTPException, status

from app.dependencies import CurrentPrincipal, DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository
from app.repositories.game_repository import GameRepository
from app.services.i18n import translate_value
from app.services.ws_client import WsEventPublisher


ACCESS_ADMIN_LABEL = "[ADMIN]"
ACCESS_BOTH_LABEL = "[ADMIN+TEAM]"
ACCESS_TEAM_LABEL = "[TEAM]"
ACCESS_SUPER_ADMIN_LABEL = "[SUPER_ADMIN]"


class SharedModuleBase:
    def __init__(self, game_type: str, ws_publisher: WsEventPublisher, game_type_detail_key: str | None = None) -> None:
        self._game_type = game_type
        self._game_type_detail_key = game_type_detail_key or game_type
        self._ws_publisher = ws_publisher
        self._game_repository = GameRepository()
        self._state_repository = GameLogicStateRepository()

    def _require_game(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        game = self._state_repository.get_game_by_id(db, game_id)
        if game is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="game.notFound")
        if str(game.get("game_type") or "") != self._game_type:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"{self._game_type_detail_key}.gameTypeRequired")
        return game

    def _require_user_manage_access(self, db: DbSession, game_id: str, principal: CurrentPrincipal) -> None:
        if principal.principal_type != "user":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="game.auth.userRequired")

        is_owner = self._game_repository.isGameOwnerByGameIdAndUserId(db, game_id, principal.principal_id)
        is_admin = self._game_repository.hasGameManagerByGameIdAndUserId(db, game_id, principal.principal_id)
        if is_owner or is_admin:
            return

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="game.auth.manageAccessRequired")

    def _require_team_self_or_manage_access(
        self,
        db: DbSession,
        game_id: str,
        team_id: str,
        principal: CurrentPrincipal,
    ) -> None:
        team = self._state_repository.get_team_by_game_and_id(db, game_id, team_id)
        if team is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="team.notFound")

        if principal.principal_type == "team":
            if principal.principal_id != team_id:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="team.auth.selfRequired")
            return

        self._require_user_manage_access(db, game_id, principal)

    def _publish_event(self, event_name: str, payload: Dict[str, Any], principal: CurrentPrincipal) -> None:
        self._ws_publisher.publish(event=event_name, payload=payload)

    @staticmethod
    def _ensure_user_principal(principal: CurrentPrincipal, detail_key: str = "game.auth.userRequired") -> None:
        if principal.principal_type != "user":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=detail_key,
            )

    @staticmethod
    def _to_db_datetime(value: datetime) -> datetime:
        if value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    @staticmethod
    def _serialize_value(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        return value

    def _serialize_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        return {key: self._serialize_value(value) for key, value in row.items()}

    @staticmethod
    def _localize_message_key(message_key: str, locale: str) -> str:
        return translate_value(message_key, locale=locale)
