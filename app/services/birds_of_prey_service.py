from datetime import UTC, datetime
from math import atan2, cos, radians, sin, sqrt
from typing import Any, Dict, Optional
from uuid import uuid4

from app.dependencies import DbSession
from app.repositories.birds_of_prey_repository import BirdsOfPreyRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class BirdsOfPreyService(GameLogicService):
    _EGG_DROP_ACTION = "birds_of_prey.egg.drop"
    _EGG_DESTROY_ACTION = "birds_of_prey.egg.destroy"
    _EARTH_RADIUS_METERS = 6371000.0

    def __init__(self) -> None:
        super().__init__("birds_of_prey", repository=BirdsOfPreyRepository())

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if numeric == numeric else None

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _distance_meters(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
        lat1 = radians(lat_a)
        lon1 = radians(lon_a)
        lat2 = radians(lat_b)
        lon2 = radians(lon_b)

        dlat = lat2 - lat1
        dlon = lon2 - lon1
        hav = (sin(dlat / 2) ** 2) + (cos(lat1) * cos(lat2) * (sin(dlon / 2) ** 2))
        c = 2 * atan2(sqrt(hav), sqrt(1 - hav))
        return BirdsOfPreyService._EARTH_RADIUS_METERS * c

    @staticmethod
    def _parse_timestamp(raw: Any) -> Optional[datetime]:
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

    def _configuration(self, db: DbSession, game_id: str) -> Dict[str, int]:
        config = self._repository.get_configuration(db, game_id)
        return {
            "visibility_radius_meters": max(10, self._safe_int(config.get("visibility_radius_meters"), 100)),
            "protection_radius_meters": max(5, self._safe_int(config.get("protection_radius_meters"), 50)),
            "auto_drop_seconds": max(30, self._safe_int(config.get("auto_drop_seconds"), 300)),
        }

    def _extract_active_eggs(self, game_state: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        actions = game_state.get("actions")
        if not isinstance(actions, list):
            return {}

        eggs: Dict[str, Dict[str, Any]] = {}
        for action in actions:
            if not isinstance(action, dict):
                continue

            action_name = str(action.get("action") or "").strip()
            egg_id = str(action.get("object_id") or "").strip()
            if not egg_id:
                continue

            metadata = action.get("metadata") if isinstance(action.get("metadata"), dict) else {}

            if action_name == self._EGG_DROP_ACTION:
                latitude = self._safe_float(metadata.get("latitude"))
                longitude = self._safe_float(metadata.get("longitude"))
                if latitude is None or longitude is None:
                    continue

                eggs[egg_id] = {
                    "id": egg_id,
                    "owner_team_id": str(metadata.get("owner_team_id") or action.get("team_id") or "").strip(),
                    "lat": latitude,
                    "lon": longitude,
                    "dropped_at": str(action.get("at") or ""),
                    "automatic": bool(metadata.get("automatic")),
                }
                continue

            if action_name == self._EGG_DESTROY_ACTION:
                eggs.pop(egg_id, None)

        return eggs

    def _get_visible_enemy_eggs(
        self,
        *,
        viewer_team_id: str,
        viewer_location: Dict[str, Any],
        eggs: Dict[str, Dict[str, Any]],
        team_locations: Dict[str, Dict[str, Any]],
        team_names: Dict[str, str],
        visibility_radius_meters: int,
        protection_radius_meters: int,
    ) -> list[Dict[str, Any]]:
        viewer_lat = self._safe_float(viewer_location.get("lat"))
        viewer_lon = self._safe_float(viewer_location.get("lon"))
        if viewer_lat is None or viewer_lon is None:
            return []

        visible: list[Dict[str, Any]] = []
        for egg in eggs.values():
            owner_team_id = str(egg.get("owner_team_id") or "").strip()
            if not owner_team_id or owner_team_id == viewer_team_id:
                continue

            egg_lat = self._safe_float(egg.get("lat"))
            egg_lon = self._safe_float(egg.get("lon"))
            if egg_lat is None or egg_lon is None:
                continue

            distance_to_egg = self._distance_meters(viewer_lat, viewer_lon, egg_lat, egg_lon)
            if distance_to_egg > float(visibility_radius_meters):
                continue

            owner_location = team_locations.get(owner_team_id) or {}
            owner_lat = self._safe_float(owner_location.get("lat"))
            owner_lon = self._safe_float(owner_location.get("lon"))
            owner_nearby = False
            if owner_lat is not None and owner_lon is not None:
                owner_distance = self._distance_meters(owner_lat, owner_lon, egg_lat, egg_lon)
                owner_nearby = owner_distance <= float(protection_radius_meters)

            visible.append({
                "id": str(egg.get("id") or ""),
                "owner_team_id": owner_team_id,
                "owner_team_name": team_names.get(owner_team_id, owner_team_id),
                "lat": egg_lat,
                "lon": egg_lon,
                "dropped_at": str(egg.get("dropped_at") or ""),
                "can_destroy": not owner_nearby,
            })

        visible.sort(key=lambda row: str(row.get("dropped_at") or ""), reverse=True)
        return visible

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)
        config = self._configuration(db, game_id)
        eggs = self.get_active_eggs(db, game_id=game_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        team_locations = self._repository.fetch_team_locations_by_game_id(db, game_id)

        team_names: Dict[str, str] = {}
        for team in teams:
            team_names[str(team.get("id") or "")] = str(team.get("name") or "")

        own_eggs = [egg for egg in eggs.values() if str(egg.get("owner_team_id") or "") == team_id]
        own_eggs.sort(key=lambda row: str(row.get("dropped_at") or ""), reverse=True)

        viewer_location = team_locations.get(team_id) or {"lat": None, "lon": None, "updated_at": ""}
        visible_enemy_eggs = self._get_visible_enemy_eggs(
            viewer_team_id=team_id,
            viewer_location=viewer_location,
            eggs=eggs,
            team_locations=team_locations,
            team_names=team_names,
            visibility_radius_meters=config["visibility_radius_meters"],
            protection_radius_meters=config["protection_radius_meters"],
        )

        team = self._repository.get_team_by_game_and_id(db, game_id, team_id)
        score = int((team or {}).get("geo_score") or 0)

        leaderboard = [
            {
                "team_id": str(team_row.get("id") or ""),
                "name": str(team_row.get("name") or ""),
                "logo_path": str(team_row.get("logo_path") or ""),
                "score": int(team_row.get("geo_score") or 0),
                "egg_count": sum(1 for egg in eggs.values() if str(egg.get("owner_team_id") or "") == str(team_row.get("id") or "")),
            }
            for team_row in teams
        ]
        leaderboard.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("name") or "").lower()))

        return {
            "version": int(game_state.get("version") or 0),
            "team_id": team_id,
            "score": score,
            "actions": int(self._team_state_entry(game_state, team_id).get("actions") or 0),
            "last_action_at": self._team_state_entry(game_state, team_id).get("last_action_at"),
            "team_location": viewer_location,
            "own_eggs": own_eggs,
            "visible_enemy_eggs": visible_enemy_eggs,
            "leaderboard": leaderboard,
            "visibility_radius_meters": config["visibility_radius_meters"],
            "protection_radius_meters": config["protection_radius_meters"],
            "auto_drop_seconds": config["auto_drop_seconds"],
        }

    def get_admin_overview(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)
        config = self._configuration(db, game_id)
        eggs = self.get_active_eggs(db, game_id=game_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        team_locations = self._repository.fetch_team_locations_by_game_id(db, game_id)

        team_names: Dict[str, str] = {}
        for team in teams:
            team_id = str(team.get("id") or "")
            team_names[team_id] = str(team.get("name") or "")

        team_rows: list[Dict[str, Any]] = []
        for team in teams:
            team_id = str(team.get("id") or "")
            location = team_locations.get(team_id) or {}
            team_rows.append({
                "team_id": team_id,
                "name": str(team.get("name") or ""),
                "logo_path": str(team.get("logo_path") or ""),
                "score": int(team.get("geo_score") or 0),
                "lat": self._safe_float(location.get("lat")),
                "lon": self._safe_float(location.get("lon")),
                "location_updated_at": str(location.get("updated_at") or ""),
                "egg_count": sum(1 for egg in eggs.values() if str(egg.get("owner_team_id") or "") == team_id),
            })

        egg_rows: list[Dict[str, Any]] = []
        for egg in eggs.values():
            owner_team_id = str(egg.get("owner_team_id") or "")
            egg_rows.append({
                "id": str(egg.get("id") or ""),
                "owner_team_id": owner_team_id,
                "owner_team_name": team_names.get(owner_team_id, owner_team_id),
                "lat": self._safe_float(egg.get("lat")),
                "lon": self._safe_float(egg.get("lon")),
                "dropped_at": str(egg.get("dropped_at") or ""),
                "automatic": bool(egg.get("automatic")),
            })

        egg_rows.sort(key=lambda row: str(row.get("dropped_at") or ""), reverse=True)
        team_rows.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("name") or "").lower()))

        return {
            "version": int(game_state.get("version") or 0),
            "config": config,
            "teams": team_rows,
            "eggs": egg_rows,
        }

    def update_team_location(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        latitude: float,
        longitude: float,
    ) -> Dict[str, Any]:
        lat = self._safe_float(latitude)
        lon = self._safe_float(longitude)
        if lat is None or lon is None or lat < -90 or lat > 90 or lon < -180 or lon > 180:
            raise ValueError("birds_of_prey.location.invalid")

        self._repository.update_team_location_without_commit(
            db,
            game_id,
            team_id,
            latitude=lat,
            longitude=lon,
        )
        self._repository.commit_changes(db)

        return self._repository.get_team_location(db, game_id, team_id)

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

    def get_visible_enemy_eggs_for_team(self, db: DbSession, *, game_id: str, team_id: str) -> list[Dict[str, Any]]:
        config = self._configuration(db, game_id)
        eggs = self.get_active_eggs(db, game_id=game_id)
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        team_locations = self._repository.fetch_team_locations_by_game_id(db, game_id)
        team_names = {str(team.get("id") or ""): str(team.get("name") or "") for team in teams}

        viewer_location = team_locations.get(team_id) or {"lat": None, "lon": None, "updated_at": ""}
        return self._get_visible_enemy_eggs(
            viewer_team_id=team_id,
            viewer_location=viewer_location,
            eggs=eggs,
            team_locations=team_locations,
            team_names=team_names,
            visibility_radius_meters=config["visibility_radius_meters"],
            protection_radius_meters=config["protection_radius_meters"],
        )

    def get_active_eggs(self, db: DbSession, *, game_id: str) -> Dict[str, Dict[str, Any]]:
        rows = self._repository.fetch_active_eggs_by_game_id(db, game_id)
        eggs: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            egg_id = str(row.get("id") or "").strip()
            if not egg_id:
                continue
            eggs[egg_id] = {
                "id": egg_id,
                "owner_team_id": str(row.get("owner_team_id") or "").strip(),
                "lat": self._safe_float(row.get("lat")),
                "lon": self._safe_float(row.get("lon")),
                "dropped_at": str(row.get("dropped_at") or ""),
                "automatic": bool(row.get("automatic")),
            }
        return eggs

    def get_last_drop_at_for_team(self, db: DbSession, *, game_id: str, team_id: str) -> Optional[datetime]:
        return self._repository.get_last_drop_at_for_team(db, game_id=game_id, team_id=team_id)

    def drop_egg(self, db: DbSession, *, game_id: str, team_id: str, egg_id: str = "", automatic: bool = False) -> GameActionResult:
        location = self._repository.get_team_location(db, game_id, team_id)
        latitude = self._safe_float(location.get("lat"))
        longitude = self._safe_float(location.get("lon"))
        if latitude is None or longitude is None:
            raise ValueError("birds_of_prey.location.required")

        normalized_egg_id = str(egg_id or "").strip() or str(uuid4())
        try:
            self._repository.insert_egg_without_commit(
                db,
                egg_id=normalized_egg_id,
                game_id=game_id,
                owner_team_id=team_id,
                latitude=latitude,
                longitude=longitude,
                automatic=bool(automatic),
            )
            self._repository.commit_changes(db)
        except Exception as error:
            self._repository.rollback_on_error(db, error)
            existing = self._repository.get_active_egg_by_id(db, game_id, normalized_egg_id)
            if isinstance(existing, dict):
                raise ValueError("birds_of_prey.egg.alreadyDropped") from error
            raise ValueError("birds_of_prey.egg.dropFailed") from error

        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name=self._EGG_DROP_ACTION,
            object_id=normalized_egg_id,
            points_awarded=0,
            allow_repeat=True,
            metadata={
                "egg_id": normalized_egg_id,
                "automatic": bool(automatic),
            },
            success_message_key="birds_of_prey.egg.dropped",
            already_message_key="birds_of_prey.egg.alreadyDropped",
        )

    def destroy_egg(self, db: DbSession, *, game_id: str, team_id: str, egg_id: str, points: int = 1) -> GameActionResult:
        config = self._configuration(db, game_id)
        eggs = self.get_active_eggs(db, game_id=game_id)
        egg = eggs.get(str(egg_id or "").strip())
        if not isinstance(egg, dict):
            raise ValueError("birds_of_prey.egg.notFound")

        owner_team_id = str(egg.get("owner_team_id") or "").strip()
        if owner_team_id == team_id:
            raise ValueError("birds_of_prey.egg.cannotDestroyOwn")

        team_locations = self._repository.fetch_team_locations_by_game_id(db, game_id)
        attacker_location = team_locations.get(team_id) or {}
        attacker_lat = self._safe_float(attacker_location.get("lat"))
        attacker_lon = self._safe_float(attacker_location.get("lon"))
        egg_lat = self._safe_float(egg.get("lat"))
        egg_lon = self._safe_float(egg.get("lon"))
        if attacker_lat is None or attacker_lon is None or egg_lat is None or egg_lon is None:
            raise ValueError("birds_of_prey.location.required")

        if self._distance_meters(attacker_lat, attacker_lon, egg_lat, egg_lon) > float(config["visibility_radius_meters"]):
            raise ValueError("birds_of_prey.egg.notVisible")

        owner_location = team_locations.get(owner_team_id) or {}
        owner_lat = self._safe_float(owner_location.get("lat"))
        owner_lon = self._safe_float(owner_location.get("lon"))
        if owner_lat is not None and owner_lon is not None:
            if self._distance_meters(owner_lat, owner_lon, egg_lat, egg_lon) <= float(config["protection_radius_meters"]):
                raise ValueError("birds_of_prey.egg.protected")

        if not self._repository.mark_egg_destroyed_without_commit(
            db,
            egg_id=str(egg_id or "").strip(),
            destroyed_by_team_id=team_id,
        ):
            raise ValueError("birds_of_prey.egg.alreadyDestroyed")

        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name=self._EGG_DESTROY_ACTION,
            object_id=egg_id,
            points_awarded=max(0, int(points)),
            allow_repeat=True,
            metadata={
                "owner_team_id": owner_team_id,
            },
            success_message_key="birds_of_prey.egg.destroyed",
            already_message_key="birds_of_prey.egg.alreadyDestroyed",
        )
