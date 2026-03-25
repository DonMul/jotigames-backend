from typing import Any, Dict

from app.dependencies import DbSession
from app.repositories.courier_rush_repository import CourierRushRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class CourierRushService(GameLogicService):
    """Coordinates gameplay actions for the Courier Rush game mode."""

    def __init__(self) -> None:
        """Construct the service with the Courier Rush repository."""
        super().__init__("courier_rush", repository=CourierRushRepository())

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Include pickups, dropoffs and highscore in team bootstrap."""
        base = super().get_team_bootstrap(db, game_id, team_id)
        pickups = self._repository.fetch_pickups_by_game_id(db, game_id)
        dropoffs = self._repository.fetch_dropoffs_by_game_id(db, game_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        base["pickups"] = [
            {
                "id": str(p.get("id", "")),
                "title": str(p.get("title", "")),
                "latitude": float(p.get("latitude") or 0),
                "longitude": float(p.get("longitude") or 0),
                "radius_meters": int(p.get("radius_meters") or 25),
                "points": int(p.get("points") or 0),
                "marker_color": str(p.get("marker_color") or "#3b82f6"),
                "is_active": bool(p.get("is_active", True)),
            }
            for p in pickups
        ]
        base["dropoffs"] = [
            {
                "id": str(d.get("id", "")),
                "title": str(d.get("title", "")),
                "latitude": float(d.get("latitude") or 0),
                "longitude": float(d.get("longitude") or 0),
                "radius_meters": int(d.get("radius_meters") or 25),
                "points": int(d.get("points") or 0),
                "marker_color": str(d.get("marker_color") or "#16a34a"),
                "is_active": bool(d.get("is_active", True)),
            }
            for d in dropoffs
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

    def confirm_pickup(self, db: DbSession, *, game_id: str, team_id: str, pickup_id: str) -> GameActionResult:
        """Persist a one-time pickup confirmation action for a team."""
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="courier_rush.pickup.confirm",
            object_id=pickup_id,
            points_awarded=0,
            allow_repeat=False,
            success_message_key="courier_rush.pickup.confirmed",
            already_message_key="courier_rush.pickup.alreadyConfirmed",
        )

    def confirm_dropoff(self, db: DbSession, *, game_id: str, team_id: str, dropoff_id: str, points: int = 1) -> GameActionResult:
        """Persist a dropoff confirmation and award points for completion."""
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="courier_rush.dropoff.confirm",
            object_id=dropoff_id,
            points_awarded=max(0, int(points)),
            allow_repeat=True,
            success_message_key="courier_rush.dropoff.confirmed",
            already_message_key="courier_rush.dropoff.confirmed",
        )
