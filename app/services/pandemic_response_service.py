from typing import Any, Dict

from app.dependencies import DbSession
from app.repositories.pandemic_response_repository import PandemicResponseRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class PandemicResponseService(GameLogicService):
    def __init__(self) -> None:
        """Initialize Pandemic Response game-logic service."""
        super().__init__("pandemic_response", repository=PandemicResponseRepository())

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Include hotspots, pickups and highscore in team bootstrap."""
        base = super().get_team_bootstrap(db, game_id, team_id)
        hotspots = self._repository.fetch_hotspots_by_game_id(db, game_id)
        pickups = self._repository.fetch_pickups_by_game_id(db, game_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        base["hotspots"] = [
            {
                "id": str(h.get("id", "")),
                "title": str(h.get("title", "")),
                "latitude": float(h.get("latitude") or 0),
                "longitude": float(h.get("longitude") or 0),
                "radius_meters": int(h.get("radius_meters") or 25),
                "points": int(h.get("points") or 0),
                "severity": str(h.get("severity") or "medium"),
                "marker_color": str(h.get("marker_color") or "#dc2626"),
                "is_active": bool(h.get("is_active", True)),
            }
            for h in hotspots
        ]
        base["pickups"] = [
            {
                "id": str(p.get("id", "")),
                "title": str(p.get("title", "")),
                "latitude": float(p.get("latitude") or 0),
                "longitude": float(p.get("longitude") or 0),
                "radius_meters": int(p.get("radius_meters") or 25),
                "marker_color": str(p.get("marker_color") or "#3b82f6"),
                "is_active": bool(p.get("is_active", True)),
            }
            for p in pickups
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

    def collect_pickup(self, db: DbSession, *, game_id: str, team_id: str, pickup_id: str) -> GameActionResult:
        """Record one-time pickup collection event."""
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="pandemic_response.pickup.collect",
            object_id=pickup_id,
            points_awarded=0,
            allow_repeat=False,
            success_message_key="pandemic_response.pickup.collected",
            already_message_key="pandemic_response.pickup.alreadyCollected",
        )

    def resolve_hotspot(self, db: DbSession, *, game_id: str, team_id: str, hotspot_id: str, points: int = 1) -> GameActionResult:
        """Record one-time hotspot resolution event and award points."""
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="pandemic_response.hotspot.resolve",
            object_id=hotspot_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="pandemic_response.hotspot.resolved",
            already_message_key="pandemic_response.hotspot.alreadyResolved",
        )
