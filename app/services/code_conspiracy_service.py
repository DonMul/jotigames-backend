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

    def submit_code(self, db: DbSession, *, game_id: str, team_id: str, target_team_id: str, code_value: str) -> GameActionResult:
        """Validate a code submission server-side against the target team's secret code.

        When the ``code_conspiracy_team_code`` table contains a stored code for
        the target team, correctness and points are determined entirely
        server-side using the game configuration (``correct_points``,
        ``penalty_enabled``, ``penalty_value``).  When no stored code is
        available the submission is recorded with zero points.
        """
        config = self._repository.get_configuration(db, game_id)
        stored_code = self._repository.get_team_code(db, game_id, target_team_id)

        normalized_submission = code_value.strip().lower()
        correct = False
        points_delta = 0

        if stored_code is not None:
            correct = normalized_submission == stored_code.lower()
            if correct:
                points_delta = max(0, int(config.get("correct_points") or 10))
            elif config.get("penalty_enabled"):
                points_delta = -abs(int(config.get("penalty_value") or 0))

        object_id = f"{target_team_id}:{normalized_submission}"
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="code_conspiracy.code.submit",
            object_id=object_id,
            points_awarded=int(points_delta),
            allow_repeat=True,
            metadata={"target_team_id": target_team_id, "correct": correct},
            success_message_key="code_conspiracy.code.submitted",
            already_message_key="code_conspiracy.code.submitted",
        )
