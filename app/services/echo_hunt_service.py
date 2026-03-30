from datetime import UTC, datetime
from math import atan2, cos, radians, sin, sqrt
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

    def claim_beacon(self, db: DbSession, *, game_id: str, team_id: str, beacon_id: str) -> GameActionResult:
        """Record one-time beacon claim and award server-configured points."""
        beacon = self._repository.get_beacon_by_game_id_and_beacon_id(db, game_id, beacon_id)
        if beacon is None:
            raise ValueError("echo_hunt.beacon.notFound")
        server_points = max(0, int(beacon.get("points") or 0))
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="echo_hunt.beacon.claim",
            object_id=beacon_id,
            points_awarded=server_points,
            allow_repeat=False,
            success_message_key="echo_hunt.beacon.claimed",
            already_message_key="echo_hunt.beacon.alreadyClaimed",
        )

    @staticmethod
    def _parse_timestamp(raw: Any) -> datetime | None:
        if isinstance(raw, datetime):
            return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
        value = str(raw or "").strip()
        if not value:
            return None
        normalized = value.replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        if "+" not in normalized and "T" in normalized:
            normalized = f"{normalized}+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return parsed.astimezone(UTC)

    @staticmethod
    def _haversine_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        radius = 6371000.0
        delta_lat = radians(lat2 - lat1)
        delta_lon = radians(lon2 - lon1)
        base = sin(delta_lat / 2.0) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_lon / 2.0) ** 2
        return 2.0 * radius * atan2(sqrt(base), sqrt(1.0 - base))

    def should_publish_location_event(self, db: DbSession, *, game_id: str, team_id: str, min_interval_seconds: int = 10) -> bool:
        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)
        entry = self._team_state_entry(game_state, team_id)
        now = datetime.now(UTC)
        previous_at = self._parse_timestamp(entry.get("last_location_publish_at"))
        if previous_at is not None and (now - previous_at).total_seconds() < float(min_interval_seconds):
            return False
        entry["last_location_publish_at"] = now.isoformat()
        self._repository.update_game_settings_without_commit(db, game_id, settings)
        self._repository.commit_changes(db)
        return True

    def update_team_location(self, db: DbSession, *, game_id: str, team_id: str, latitude: float, longitude: float) -> Dict[str, Any]:
        lat = self._repository._safe_float(latitude)
        lon = self._repository._safe_float(longitude)
        if lat is None or lon is None or lat < -90 or lat > 90 or lon < -180 or lon > 180:
            raise ValueError("echo_hunt.location.invalid")
        self._repository.update_team_location_without_commit(db, game_id, team_id, latitude=lat, longitude=lon)
        self._repository.commit_changes(db)
        return self._repository.get_team_location(db, game_id, team_id)

    def get_nearby_beacons_for_team(self, db: DbSession, game_id: str, team_id: str) -> list[Dict[str, Any]]:
        location = self._repository.get_team_location(db, game_id, team_id)
        lat = self._repository._safe_float(location.get("lat"))
        lon = self._repository._safe_float(location.get("lon"))
        if lat is None or lon is None:
            return []

        nearby: list[Dict[str, Any]] = []
        for beacon in self._repository.fetch_beacons_by_game_id(db, game_id):
            beacon_lat = self._repository._safe_float(beacon.get("latitude"))
            beacon_lon = self._repository._safe_float(beacon.get("longitude"))
            if beacon_lat is None or beacon_lon is None or not bool(beacon.get("is_active", True)):
                continue
            radius = max(1, int(beacon.get("radius_meters") or 25))
            if self._haversine_meters(lat, lon, beacon_lat, beacon_lon) <= float(radius):
                nearby.append({
                    "id": str(beacon.get("id") or ""),
                    "title": str(beacon.get("title") or ""),
                })
        return nearby
