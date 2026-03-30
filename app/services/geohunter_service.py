from datetime import UTC, datetime
from math import atan2, cos, radians, sin, sqrt
from typing import Any, Dict

from app.dependencies import DbSession
from app.repositories.geohunter_repository import GeoHunterRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class GeoHunterService(GameLogicService):
    def __init__(self) -> None:
        """Initialize GeoHunter game-logic service with repository wiring."""
        super().__init__("geohunter", repository=GeoHunterRepository())

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Include POIs with choices and highscore in team bootstrap."""
        base = super().get_team_bootstrap(db, game_id, team_id)
        pois = self._repository.fetch_pois_by_game_id(db, game_id)
        poi_ids = [str(p.get("id", "")) for p in pois]
        choices_map = self._repository.fetch_choices_by_poi_ids(db, poi_ids) if poi_ids else {}
        teams = self._repository.fetch_teams_by_game_id(db, game_id)

        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)
        retry_enabled = bool(game_state.get("retry_enabled", False))
        retry_timeout_seconds = int(game_state.get("retry_timeout_seconds") or 60)

        base["pois"] = [
            {
                "id": str(p.get("id", "")),
                "title": str(p.get("title", "")),
                "latitude": float(p.get("latitude") or 0),
                "longitude": float(p.get("longitude") or 0),
                "radius_meters": int(p.get("radius_meters") or 25),
                "points": int(p.get("points") or 0),
                "marker_color": str(p.get("marker_color") or "#10b981"),
                "is_active": bool(p.get("is_active", True)),
                "question_type": str(p.get("question_type") or "open"),
                "question_text": str(p.get("question_text") or ""),
                "choices": [
                    {
                        "id": str(c.get("id", "")),
                        "label": str(c.get("label", "")),
                    }
                    for c in choices_map.get(str(p.get("id", "")), [])
                ],
            }
            for p in pois
        ]
        base["retry_enabled"] = retry_enabled
        base["retry_timeout_seconds"] = retry_timeout_seconds
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

    def answer_question(self, db: DbSession, *, game_id: str, team_id: str, poi_id: str, answer: str) -> GameActionResult:
        """Validate answer server-side against the POI's expected answer and award configured points."""
        poi = self._repository.get_poi_by_game_id_and_poi_id(db, game_id, poi_id)
        if poi is None:
            raise ValueError("geohunter.poi.notFound")

        question_type = str(poi.get("question_type") or "open").strip().lower()
        server_points = max(0, int(poi.get("points") or 0))
        submitted = str(answer or "").strip()

        if question_type == "multiple_choice":
            # answer must be a choice id; look up that choice and check is_correct
            choices = self._repository.fetch_choices_by_poi_ids(db, [poi_id]).get(poi_id, [])
            matched_choice = next(
                (c for c in choices if str(c.get("id", "")).strip() == submitted),
                None,
            )
            correct = matched_choice is not None and bool(matched_choice.get("is_correct", False))
        else:
            # open answer: case-insensitive comparison with configured correct_answer
            expected = str(poi.get("correct_answer") or "").strip().lower()
            correct = submitted.lower() == expected and expected != ""

        points = server_points if correct else 0
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="geohunter.question.answer",
            object_id=poi_id,
            points_awarded=points,
            allow_repeat=False,
            metadata={"correct": correct, "answer": submitted},
            success_message_key="geohunter.answer.recorded",
            already_message_key="geohunter.answer.alreadySubmitted",
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
            raise ValueError("geohunter.location.invalid")
        self._repository.update_team_location_without_commit(db, game_id, team_id, latitude=lat, longitude=lon)
        self._repository.commit_changes(db)
        return self._repository.get_team_location(db, game_id, team_id)

    def get_nearby_pois_for_team(self, db: DbSession, game_id: str, team_id: str) -> list[Dict[str, Any]]:
        location = self._repository.get_team_location(db, game_id, team_id)
        lat = self._repository._safe_float(location.get("lat"))
        lon = self._repository._safe_float(location.get("lon"))
        if lat is None or lon is None:
            return []

        nearby: list[Dict[str, Any]] = []
        for poi in self._repository.fetch_pois_by_game_id(db, game_id):
            poi_lat = self._repository._safe_float(poi.get("latitude"))
            poi_lon = self._repository._safe_float(poi.get("longitude"))
            if poi_lat is None or poi_lon is None or not bool(poi.get("is_active", True)):
                continue
            radius = max(1, int(poi.get("radius_meters") or 25))
            if self._haversine_meters(lat, lon, poi_lat, poi_lon) <= float(radius):
                nearby.append({
                    "id": str(poi.get("id") or ""),
                    "title": str(poi.get("title") or ""),
                })
        return nearby
