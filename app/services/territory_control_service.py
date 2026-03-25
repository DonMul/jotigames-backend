from typing import Any, Dict

from app.dependencies import DbSession
from app.repositories.territory_control_repository import TerritoryControlRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class TerritoryControlService(GameLogicService):
    def __init__(self) -> None:
        """Initialize Territory Control game-logic service."""
        super().__init__("territory_control", repository=TerritoryControlRepository())

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Include zones and highscore in team bootstrap."""
        base = super().get_team_bootstrap(db, game_id, team_id)
        zones = self._repository.fetch_zones_by_game_id(db, game_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        base["zones"] = [
            {
                "id": str(z.get("id", "")),
                "title": str(z.get("title", "")),
                "latitude": float(z.get("latitude") or 0),
                "longitude": float(z.get("longitude") or 0),
                "radius_meters": int(z.get("radius_meters") or 50),
                "points": int(z.get("points") or 0),
                "marker_color": str(z.get("marker_color") or "#8b5cf6"),
                "is_active": bool(z.get("is_active", True)),
            }
            for z in zones
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

    def claim_zone(self, db: DbSession, *, game_id: str, team_id: str, zone_id: str, points: int = 1) -> GameActionResult:
        """Record one-time zone claim action and award capture points."""
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="territory_control.poi.claim",
            object_id=zone_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="territory_control.claim.recorded",
            already_message_key="territory_control.claim.alreadyOwned",
        )
