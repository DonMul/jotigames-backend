from typing import Any, Dict

from app.dependencies import DbSession
from app.repositories.code_conspiracy_repository import CodeConspiracyRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class CodeConspiracyService(GameLogicService):
    """Implements action-level behavior for the Code Conspiracy game mode."""

    def __init__(self) -> None:
        """Initialize the service with the Code Conspiracy state repository."""
        super().__init__("code_conspiracy", repository=CodeConspiracyRepository())

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Include config and highscore in team bootstrap."""
        base = super().get_team_bootstrap(db, game_id, team_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)

        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)
        base["config"] = {
            "rounds": int(game_state.get("rounds") or 3),
            "code_length": int(game_state.get("code_length") or 4),
            "max_attempts": int(game_state.get("max_attempts") or 10),
        }
        base["teams_list"] = [
            {
                "team_id": str(t.get("id", "")),
                "name": str(t.get("name", "")),
                "logo_path": str(t.get("logo_path") or ""),
            }
            for t in teams
            if str(t.get("id", "")) != team_id
        ]
        base["highscore"] = [
            {
                "team_id": str(t.get("id", "")),
                "name": str(t.get("name", "")),
                "logo_path": str(t.get("logo_path") or ""),
                "score": int(t.get("geo_score") or 0),
            }
            for t in teams
        ]
        return base

    def submit_code(self, db: DbSession, *, game_id: str, team_id: str, target_team_id: str, code_value: str, points_delta: int = 0) -> GameActionResult:
        """Record a code submission attempt against a target team.

        The submission is modeled as a repeatable action claim so teams can submit
        multiple guesses over time. The action object identifier combines target
        team and normalized code value for traceability in the audit trail.
        """
        object_id = f"{target_team_id}:{code_value.strip().lower()}"
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="code_conspiracy.code.submit",
            object_id=object_id,
            points_awarded=int(points_delta),
            allow_repeat=True,
            metadata={"target_team_id": target_team_id},
            success_message_key="code_conspiracy.code.submitted",
            already_message_key="code_conspiracy.code.submitted",
        )
