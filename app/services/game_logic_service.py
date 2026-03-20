from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, Optional

from app.dependencies import DbSession
from app.repositories.game_logic_state_repository import GameLogicStateRepository


@dataclass
class GameActionResult:
    success: bool
    message_key: str
    action_id: str
    points_awarded: int
    state_version: int


class GameLogicService:
    _SETTINGS_ROOT = "_backend_game_logic"

    def __init__(self, game_type: str, repository: Optional[GameLogicStateRepository] = None) -> None:
        self._game_type = game_type
        self._repository = repository or GameLogicStateRepository()

    def _load_game_state(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        settings = self._repository.get_game_settings(db, game_id)
        root = settings.get(self._SETTINGS_ROOT)
        if not isinstance(root, dict):
            root = {}
        game_state = root.get(self._game_type)
        if not isinstance(game_state, dict):
            game_state = {
                "version": 0,
                "actions": [],
                "claims": {},
                "team_state": {},
            }

        root[self._game_type] = game_state
        settings[self._SETTINGS_ROOT] = root
        return settings

    def _game_state_from_settings(self, settings: Dict[str, Any]) -> Dict[str, Any]:
        return settings[self._SETTINGS_ROOT][self._game_type]

    def _team_state_entry(self, game_state: Dict[str, Any], team_id: str) -> Dict[str, Any]:
        team_state = game_state.get("team_state")
        if not isinstance(team_state, dict):
            team_state = {}
            game_state["team_state"] = team_state
        entry = team_state.get(team_id)
        if not isinstance(entry, dict):
            entry = {"score_delta": 0, "actions": 0, "last_action_at": None}
            team_state[team_id] = entry
        return entry

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)
        team_state = self._team_state_entry(game_state, team_id)
        team = self._repository.get_team_by_game_and_id(db, game_id, team_id)
        geo_score = int((team or {}).get("geo_score") or 0)

        return {
            "version": int(game_state.get("version") or 0),
            "team_id": team_id,
            "score": geo_score,
            "score_delta": int(team_state.get("score_delta") or 0),
            "actions": int(team_state.get("actions") or 0),
            "last_action_at": team_state.get("last_action_at"),
            "last_actions": list(game_state.get("actions") or [])[-10:],
        }

    def get_admin_overview(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)

        return {
            "version": int(game_state.get("version") or 0),
            "teams": [
                {
                    "team_id": str(team.get("id")),
                    "name": str(team.get("name") or ""),
                    "score": int(team.get("geo_score") or 0),
                }
                for team in teams
            ],
            "recent_actions": list(game_state.get("actions") or [])[-50:],
        }

    def apply_action(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        action_name: str,
        object_id: str,
        points_awarded: int = 0,
        allow_repeat: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
        success_message_key: str,
        already_message_key: str,
    ) -> GameActionResult:
        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)

        claims = game_state.get("claims")
        if not isinstance(claims, dict):
            claims = {}
            game_state["claims"] = claims

        claim_key = f"{action_name}:{team_id}:{object_id}"
        if not allow_repeat and claims.get(claim_key):
            return GameActionResult(
                success=True,
                message_key=already_message_key,
                action_id="",
                points_awarded=0,
                state_version=int(game_state.get("version") or 0),
            )

        now = datetime.now(UTC).isoformat()
        action_id = f"{team_id}:{action_name}:{object_id}:{int(datetime.now(UTC).timestamp())}"
        points = int(points_awarded)

        claims[claim_key] = {
            "at": now,
            "points": points,
        }

        actions = game_state.get("actions")
        if not isinstance(actions, list):
            actions = []
            game_state["actions"] = actions

        action_entry = {
            "id": action_id,
            "team_id": team_id,
            "action": action_name,
            "object_id": object_id,
            "points_awarded": points,
            "at": now,
        }
        if metadata:
            action_entry["metadata"] = metadata
        actions.append(action_entry)

        team_state = self._team_state_entry(game_state, team_id)
        team_state["actions"] = int(team_state.get("actions") or 0) + 1
        team_state["score_delta"] = int(team_state.get("score_delta") or 0) + points
        team_state["last_action_at"] = now

        next_version = int(game_state.get("version") or 0) + 1
        game_state["version"] = next_version

        if points != 0:
            self._repository.increment_team_geo_score_without_commit(db, team_id, points)

        self._repository.update_game_settings_without_commit(db, game_id, settings)
        self._repository.commit_changes(db)

        return GameActionResult(
            success=True,
            message_key=success_message_key,
            action_id=action_id,
            points_awarded=points,
            state_version=next_version,
        )
