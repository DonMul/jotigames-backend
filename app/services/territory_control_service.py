from datetime import UTC, datetime
from math import atan2, cos, radians, sin, sqrt
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

    def claim_zone(self, db: DbSession, *, game_id: str, team_id: str, zone_id: str) -> GameActionResult:
        """Record one-time zone claim action and award server-configured capture points."""
        zone = self._repository.get_zone_by_game_id_and_zone_id(db, game_id, zone_id)
        if zone is None:
            raise ValueError("territory_control.zone.notFound")
        server_points = max(0, int(zone.get("capture_points") or 0))
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="territory_control.poi.claim",
            object_id=zone_id,
            points_awarded=server_points,
            allow_repeat=False,
            success_message_key="territory_control.claim.recorded",
            already_message_key="territory_control.claim.alreadyOwned",
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
            raise ValueError("territory_control.location.invalid")
        self._repository.update_team_location_without_commit(db, game_id, team_id, latitude=lat, longitude=lon)
        self._repository.commit_changes(db)
        return self._repository.get_team_location(db, game_id, team_id)

    def get_nearby_zones_for_team(self, db: DbSession, game_id: str, team_id: str) -> list[Dict[str, Any]]:
        location = self._repository.get_team_location(db, game_id, team_id)
        lat = self._repository._safe_float(location.get("lat"))
        lon = self._repository._safe_float(location.get("lon"))
        if lat is None or lon is None:
            return []

        nearby: list[Dict[str, Any]] = []
        for zone in self._repository.fetch_zones_by_game_id(db, game_id):
            zone_lat = self._repository._safe_float(zone.get("latitude"))
            zone_lon = self._repository._safe_float(zone.get("longitude"))
            if zone_lat is None or zone_lon is None or not bool(zone.get("is_active", True)):
                continue
            radius = max(1, int(zone.get("radius_meters") or 25))
            if self._haversine_meters(lat, lon, zone_lat, zone_lon) <= float(radius):
                nearby.append({
                    "id": str(zone.get("id") or ""),
                    "title": str(zone.get("title") or ""),
                })
        return nearby
