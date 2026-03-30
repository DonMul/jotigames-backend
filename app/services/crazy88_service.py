from datetime import datetime
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
        submission_rows = self._repository.fetch_submission_threads_by_game_id_and_team_id(db, game_id, team_id)
        submissions_by_task_id: dict[str, list[dict[str, Any]]] = {}
        for row in submission_rows:
            task_key = str(row.get("task_id") or "")
            if not task_key:
                continue
            history = submissions_by_task_id.setdefault(task_key, [])
            history.append(
                {
                    "id": str(row.get("id") or ""),
                    "status": str(row.get("status") or "pending"),
                    "submitted_at": self._to_iso(row.get("submitted_at")),
                    "reviewed_at": self._to_iso(row.get("reviewed_at")),
                    "team_message": row.get("team_message"),
                    "judge_message": row.get("judge_message"),
                    "proof_path": row.get("proof_path"),
                    "proof_original_name": row.get("proof_original_name"),
                    "proof_mime_type": row.get("proof_mime_type"),
                    "proof_size": None if row.get("proof_size") is None else int(row.get("proof_size") or 0),
                    "proof_text": row.get("proof_text"),
                }
            )
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
                "latitude": None if task.get("latitude") is None else float(task.get("latitude")),
                "longitude": None if task.get("longitude") is None else float(task.get("longitude")),
                "radius_meters": int(task.get("radius_meters") or 25),
                "sort_order": int(task.get("sort_order") or 0),
                "submissions": submissions_by_task_id.get(str(task.get("id") or ""), []),
                "latest_status": (
                    submissions_by_task_id.get(str(task.get("id") or ""), [])[-1].get("status")
                    if submissions_by_task_id.get(str(task.get("id") or ""), [])
                    else None
                ),
                "can_submit": (
                    not submissions_by_task_id.get(str(task.get("id") or ""), [])
                    or str(submissions_by_task_id.get(str(task.get("id") or ""), [])[-1].get("status") or "").lower() == "rejected"
                ),
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

    @staticmethod
    def _to_iso(value: Any) -> str | None:
        """Convert a timestamp-like value to ISO text."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.isoformat()
        text = str(value).strip()
        return text or None

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
