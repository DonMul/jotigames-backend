from typing import Any, Dict

from app.dependencies import DbSession
from app.repositories.echo_hunt_repository import EchoHuntRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class EchoHuntService(GameLogicService):
    """Action service for Echo Hunt beacon claim interactions."""

    def __init__(self) -> None:
        """Initialize Echo Hunt game-logic service."""
        super().__init__("echo_hunt", repository=EchoHuntRepository())

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Include active beacons and highscore in team bootstrap."""
        base = super().get_team_bootstrap(db, game_id, team_id)
        beacons = self._repository.fetch_beacons_by_game_id(db, game_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        base["beacons"] = [
            {
                "id": str(b.get("id", "")),
                "title": str(b.get("title", "")),
                "latitude": float(b.get("latitude") or 0),
                "longitude": float(b.get("longitude") or 0),
                "radius_meters": int(b.get("radius_meters") or 25),
                "points": int(b.get("points") or 0),
                "marker_color": str(b.get("marker_color") or "#6366f1"),
                "is_active": bool(b.get("is_active", True)),
            }
            for b in beacons
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

    def claim_beacon(self, db: DbSession, *, game_id: str, team_id: str, beacon_id: str, points: int = 1) -> GameActionResult:
        """Record one-time beacon claim and award configured points."""
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="echo_hunt.beacon.claim",
            object_id=beacon_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="echo_hunt.beacon.claimed",
            already_message_key="echo_hunt.beacon.alreadyClaimed",
        )
