from typing import Any, Dict

from app.dependencies import DbSession
from app.repositories.checkpoint_heist_repository import CheckpointHeistRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class CheckpointHeistService(GameLogicService):
    """Action service for Checkpoint Heist capture interactions."""

    def __init__(self) -> None:
        """Initialize Checkpoint Heist game-logic service."""
        super().__init__("checkpoint_heist", repository=CheckpointHeistRepository())

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Include active checkpoints and highscore in team bootstrap."""
        base = super().get_team_bootstrap(db, game_id, team_id)
        checkpoints = self._repository.fetch_checkpoints_by_game_id(db, game_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        base["checkpoints"] = [
            {
                "id": str(cp.get("id", "")),
                "title": str(cp.get("title", "")),
                "latitude": float(cp.get("latitude") or 0),
                "longitude": float(cp.get("longitude") or 0),
                "radius_meters": int(cp.get("radius_meters") or 25),
                "points": int(cp.get("points") or 0),
                "marker_color": str(cp.get("marker_color") or "#dc2626"),
                "is_active": bool(cp.get("is_active", True)),
            }
            for cp in checkpoints
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

    def capture_checkpoint(self, db: DbSession, *, game_id: str, team_id: str, checkpoint_id: str, points: int = 1) -> GameActionResult:
        """Record one-time checkpoint capture event and award points."""
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="checkpoint_heist.capture.confirm",
            object_id=checkpoint_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="checkpoint_heist.capture.recorded",
            already_message_key="checkpoint_heist.capture.alreadyCaptured",
        )
