from typing import Any, Dict

from app.dependencies import DbSession
from app.repositories.resource_run_repository import ResourceRunRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class ResourceRunService(GameLogicService):
    def __init__(self) -> None:
        """Initialize Resource Run game logic service."""
        super().__init__("resource_run", repository=ResourceRunRepository())

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Include resource nodes and highscore in team bootstrap."""
        base = super().get_team_bootstrap(db, game_id, team_id)
        nodes = self._repository.fetch_nodes_by_game_id(db, game_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        base["nodes"] = [
            {
                "id": str(n.get("id", "")),
                "title": str(n.get("title", "")),
                "latitude": float(n.get("latitude") or 0),
                "longitude": float(n.get("longitude") or 0),
                "radius_meters": int(n.get("radius_meters") or 25),
                "points": int(n.get("points") or 0),
                "marker_color": str(n.get("marker_color") or "#f59e0b"),
                "is_active": bool(n.get("is_active", True)),
            }
            for n in nodes
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

    def claim_resource(self, db: DbSession, *, game_id: str, team_id: str, node_id: str, points: int = 1) -> GameActionResult:
        """Record one-time resource claim action and award configured points."""
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="resource_run.resource.claim",
            object_id=node_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="resource_run.claim.recorded",
            already_message_key="resource_run.claim.alreadyCollected",
        )
