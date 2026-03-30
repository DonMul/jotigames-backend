from typing import Any, Dict

from app.dependencies import DbSession
from app.repositories.crazy88_repository import Crazy88Repository
from app.services.game_logic_service import GameActionResult, GameLogicService


class Crazy88Service(GameLogicService):
    """Handles action-log state transitions for Crazy88 interactions."""

    def __init__(self) -> None:
        """Initialize the service with the Crazy88 repository backend."""
        super().__init__("crazy_88", repository=Crazy88Repository())

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Include tasks and highscore in team bootstrap."""
        base = super().get_team_bootstrap(db, game_id, team_id)
        tasks = self._repository.fetch_tasks_by_game_id(db, game_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        config = self._repository.get_configuration(db, game_id)
        show_highscore = bool(config.get("show_highscore", True))
        base["tasks"] = [
            {
                "id": str(task.get("id", "")),
                "title": str(task.get("title", "")),
                "description": str(task.get("description") or ""),
                "points": int(task.get("points") or 0),
                "category": str(task.get("category") or ""),
                "is_active": False if task.get("is_active") is False else True,
            }
            for task in tasks
        ]
        base["highscore"] = [
            {
                "team_id": str(t.get("id", "")),
                "name": str(t.get("name", "")),
                "logo_path": str(t.get("logo_path") or ""),
                "score": int(t.get("geo_score") or 0),
            }
            for t in teams
        ] if show_highscore else []
        base["show_highscore"] = show_highscore
        return base

    def submit_task(self, db: DbSession, *, game_id: str, team_id: str, task_id: str) -> GameActionResult:
        """Record a task submission action for review by admins."""
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="crazy88.task.submit",
            object_id=task_id,
            points_awarded=0,
            allow_repeat=True,
            success_message_key="crazy88.task.submitted",
            already_message_key="crazy88.task.submitted",
        )

    def judge_submission(self, db: DbSession, *, game_id: str, team_id: str, submission_id: str, accepted: bool) -> GameActionResult:
        """Store the judging action and optional point award for a submission."""
        points = 1 if accepted else 0
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="crazy88.review.judge",
            object_id=submission_id,
            points_awarded=points,
            allow_repeat=False,
            metadata={"accepted": bool(accepted)},
            success_message_key="crazy88.review.judged",
            already_message_key="crazy88.review.alreadyJudged",
        )
