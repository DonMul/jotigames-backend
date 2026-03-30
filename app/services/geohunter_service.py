from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from math import atan2, cos, radians, sin, sqrt
import json
from typing import Any, Dict
from uuid import uuid4

from app.dependencies import DbSession
from app.repositories.geohunter_repository import GeoHunterRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


@dataclass
class GeoHunterAnswerOutcome:
    success: bool
    message_key: str
    action_id: str
    points_awarded: int
    state_version: int
    correct: bool
    score: int
    retry_available_in_seconds: int
    lock_active: bool = False


class GeoHunterService(GameLogicService):
    def __init__(self) -> None:
        """Initialize GeoHunter game-logic service with repository wiring."""
        super().__init__("geohunter", repository=GeoHunterRepository())

    @staticmethod
    def _parse_expected_answers(raw: Any) -> list[str]:
        if isinstance(raw, list):
            return [str(item).strip().lower() for item in raw if str(item or "").strip()]
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    return [str(item).strip().lower() for item in parsed if str(item or "").strip()]
            except Exception:
                return [str(item).strip().lower() for item in raw.split("\n") if str(item or "").strip()]
        return []

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Include POIs with choices and highscore in team bootstrap."""
        base = super().get_team_bootstrap(db, game_id, team_id)
        pois = self._repository.fetch_pois_by_game_id(db, game_id)
        poi_ids = [str(p.get("id", "")) for p in pois]
        choices_map = self._repository.fetch_choices_by_poi_ids(db, poi_ids) if poi_ids else {}
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        game = self._repository.get_game_by_id(db, game_id) or {}

        retry_enabled, retry_timeout_seconds = self._extract_retry_settings_from_game(game)
        visibility_mode = self._repository.get_visibility_mode(db, game_id)
        retry_locked_poi_seconds = self.get_retry_locked_poi_seconds_for_team(
            db,
            game_id=game_id,
            team_id=team_id,
            poi_ids=poi_ids,
            retry_enabled=retry_enabled,
            retry_timeout_seconds=retry_timeout_seconds,
        )
        retry_available_in_seconds = max(retry_locked_poi_seconds.values(), default=0)

        try:
            nearby_pois = self.get_nearby_pois_for_team(db, game_id, team_id)
        except Exception:
            nearby_pois = []
        nearby_poi_ids = [str(item.get("id") or "") for item in nearby_pois if str(item.get("id") or "")]
        nearby_poi_lockouts_seconds = {
            poi_id: retry_locked_poi_seconds[poi_id]
            for poi_id in nearby_poi_ids
            if poi_id in retry_locked_poi_seconds
        }

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
                "question_type": str(p.get("question_type") or p.get("type") or "text"),
                "question_text": str(p.get("question") or p.get("question_text") or ""),
                "content": str(p.get("content") or ""),
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
        base["poi_visibility_mode"] = visibility_mode
        base["retry_available_in_seconds"] = retry_available_in_seconds
        base["retry_locked_poi_seconds"] = retry_locked_poi_seconds
        base["nearby_poi_ids"] = nearby_poi_ids
        base["nearby_poi_lockouts_seconds"] = nearby_poi_lockouts_seconds
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

    @staticmethod
    def _extract_retry_settings_from_game(game: Dict[str, Any]) -> tuple[bool, int]:
        raw_enabled = game.get("geo_hunter_retry_enabled")
        if raw_enabled is None:
            raw_enabled = game.get("geoHunterRetryEnabled")

        raw_timeout = game.get("geo_hunter_retry_timeout_seconds")
        if raw_timeout is None:
            raw_timeout = game.get("geoHunterRetryTimeoutSeconds")

        enabled = bool(raw_enabled)
        timeout_seconds = max(0, int(raw_timeout or 0))
        return enabled, timeout_seconds

    def _team_score(self, db: DbSession, game_id: str, team_id: str) -> int:
        team = self._repository.get_team_by_game_and_id(db, game_id, team_id) or {}
        return int(team.get("geo_score") or 0)

    def _submission_is_correct(self, submission: Dict[str, Any]) -> bool:
        return bool(submission.get("is_correct", submission.get("isCorrect", False)))

    def _submission_timestamp(self, submission: Dict[str, Any]) -> datetime | None:
        return self._parse_timestamp(submission.get("submitted_at", submission.get("submittedAt")))

    def _lock_remaining_seconds(
        self,
        *,
        submission: Dict[str, Any] | None,
        retry_enabled: bool,
        retry_timeout_seconds: int,
        now: datetime,
    ) -> int:
        if submission is None or not retry_enabled or retry_timeout_seconds <= 0:
            return 0
        if self._submission_is_correct(submission):
            return 0
        submitted_at = self._submission_timestamp(submission)
        if submitted_at is None:
            return 0
        available_at = submitted_at + timedelta(seconds=retry_timeout_seconds)
        if available_at <= now:
            return 0
        return max(1, int(ceil((available_at - now).total_seconds())))

    def get_retry_locked_poi_seconds_for_team(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        poi_ids: list[str],
        retry_enabled: bool,
        retry_timeout_seconds: int,
    ) -> Dict[str, int]:
        """Return lock countdowns per POI derived from latest geo_submission rows."""
        if not poi_ids or not retry_enabled or retry_timeout_seconds <= 0:
            return {}

        submissions_by_poi = self._repository.fetch_submissions_by_team_and_poi_ids(
            db,
            team_id=team_id,
            poi_ids=poi_ids,
        )
        now = datetime.now(UTC)
        lock_map: Dict[str, int] = {}
        for poi_id in poi_ids:
            normalized_poi_id = str(poi_id or "").strip()
            if not normalized_poi_id:
                continue
            submission = submissions_by_poi.get(normalized_poi_id)
            remaining = self._lock_remaining_seconds(
                submission=submission,
                retry_enabled=retry_enabled,
                retry_timeout_seconds=retry_timeout_seconds,
                now=now,
            )
            if remaining > 0:
                lock_map[normalized_poi_id] = remaining
        return lock_map

    def answer_question(self, db: DbSession, *, game_id: str, team_id: str, poi_id: str, answer: str) -> GeoHunterAnswerOutcome:
        """Validate answer server-side against the POI's expected answer and award configured points."""
        poi = self._repository.get_poi_by_game_id_and_poi_id(db, game_id, poi_id)
        if poi is None:
            raise ValueError("geohunter.poi.notFound")

        nearby_pois = self.get_nearby_pois_for_team(db, game_id, team_id)
        nearby_poi_ids = {str(item.get("id") or "") for item in nearby_pois}
        if str(poi_id) not in nearby_poi_ids:
            return GeoHunterAnswerOutcome(
                success=False,
                message_key="geohunter.answer.outOfRange",
                action_id="",
                points_awarded=0,
                state_version=0,
                correct=False,
                score=self._team_score(db, game_id, team_id),
                retry_available_in_seconds=0,
            )

        current_score = self._team_score(db, game_id, team_id)
        question_type = str(poi.get("question_type") or "open").strip().lower()
        if question_type not in {"text", "multiple_choice", "open_answer"}:
            question_type = str(poi.get("type") or "text").strip().lower()
        server_points = max(0, int(poi.get("points") or 0))
        submitted = str(answer or "").strip()

        game = self._repository.get_game_by_id(db, game_id) or {}
        retry_enabled, retry_timeout_seconds = self._extract_retry_settings_from_game(game)
        now = datetime.now(UTC)

        existing_submission = self._repository.get_submission_by_team_and_poi(db, team_id=team_id, poi_id=poi_id)
        if existing_submission is not None and self._submission_is_correct(existing_submission):
            return GeoHunterAnswerOutcome(
                success=False,
                message_key="geohunter.answer.alreadySubmitted",
                action_id="",
                points_awarded=0,
                state_version=0,
                correct=True,
                score=current_score,
                retry_available_in_seconds=0,
            )

        if existing_submission is not None and not retry_enabled:
            return GeoHunterAnswerOutcome(
                success=False,
                message_key="geohunter.answer.alreadySubmitted",
                action_id="",
                points_awarded=0,
                state_version=0,
                correct=False,
                score=current_score,
                retry_available_in_seconds=0,
            )

        remaining_seconds = self._lock_remaining_seconds(
            submission=existing_submission,
            retry_enabled=retry_enabled,
            retry_timeout_seconds=retry_timeout_seconds,
            now=now,
        )
        if remaining_seconds > 0:
            return GeoHunterAnswerOutcome(
                success=False,
                message_key="geohunter.answer.retryTimeoutActive",
                action_id="",
                points_awarded=0,
                state_version=0,
                correct=False,
                score=current_score,
                retry_available_in_seconds=remaining_seconds,
                lock_active=True,
            )

        if question_type == "text":
            return GeoHunterAnswerOutcome(
                success=True,
                message_key="geohunter.answer.recorded",
                action_id=None,
                points_awarded=0,
                state_version=0,
                correct=True,
                score=current_score,
                retry_available_in_seconds=0,
            )

        if question_type == "multiple_choice":
            # answer must be a choice id; look up that choice and check is_correct
            choices = self._repository.fetch_choices_by_poi_ids(db, [poi_id]).get(poi_id, [])
            matched_choice = next(
                (c for c in choices if str(c.get("id", "")).strip() == submitted),
                None,
            )
            correct = matched_choice is not None and bool(matched_choice.get("is_correct", False))
        else:
            expected_answers = self._parse_expected_answers(poi.get("expected_answers"))
            submitted_normalized = submitted.lower()
            correct = submitted_normalized in expected_answers and bool(expected_answers)

        submission_table = self._repository.get_geo_submission_table(db)
        submission_values: Dict[str, Any] = {}
        if "submitted_at" in submission_table.c:
            submission_values["submitted_at"] = now.replace(tzinfo=None)
        elif "submittedAt" in submission_table.c:
            submission_values["submittedAt"] = now.replace(tzinfo=None)
        if "is_correct" in submission_table.c:
            submission_values["is_correct"] = bool(correct)
        elif "isCorrect" in submission_table.c:
            submission_values["isCorrect"] = bool(correct)
        if "points_awarded" in submission_table.c:
            submission_values["points_awarded"] = int(server_points if correct else 0)
        elif "pointsAwarded" in submission_table.c:
            submission_values["pointsAwarded"] = int(server_points if correct else 0)
        if "answer_text" in submission_table.c:
            submission_values["answer_text"] = submitted if submitted else None
        if "answerText" in submission_table.c:
            submission_values["answerText"] = submitted if submitted else None
        selected_choice_payload = json.dumps([submitted], ensure_ascii=False) if question_type == "multiple_choice" and submitted else None
        if "selected_choice_ids" in submission_table.c:
            submission_values["selected_choice_ids"] = selected_choice_payload
        if "selectedChoiceIds" in submission_table.c:
            submission_values["selectedChoiceIds"] = selected_choice_payload

        try:
            if existing_submission is None:
                insert_values = dict(submission_values)
                if "id" in submission_table.c:
                    insert_values["id"] = str(uuid4())
                if "point_id" in submission_table.c:
                    insert_values["point_id"] = poi_id
                if "pointId" in submission_table.c:
                    insert_values["pointId"] = poi_id
                if "team_id" in submission_table.c:
                    insert_values["team_id"] = team_id
                if "teamId" in submission_table.c:
                    insert_values["teamId"] = team_id
                self._repository.create_submission_without_commit(db, insert_values)
            else:
                existing_submission_id = str(existing_submission.get("id") or "").strip()
                if existing_submission_id:
                    self._repository.update_submission_without_commit(
                        db,
                        existing_submission_id,
                        submission_values,
                    )
                else:
                    self._repository.update_submission_by_team_and_poi_without_commit(
                        db,
                        team_id=team_id,
                        poi_id=poi_id,
                        values=submission_values,
                    )
            self._repository.commit_changes(db)
        except Exception as error:
            self._repository.rollback_on_error(db, error)
            raise

        if not correct:
            if retry_enabled:
                return GeoHunterAnswerOutcome(
                    success=False,
                    message_key="geohunter.answer.incorrect",
                    action_id="",
                    points_awarded=0,
                    state_version=0,
                    correct=False,
                    score=current_score,
                    retry_available_in_seconds=int(retry_timeout_seconds),
                    lock_active=False,
                )

            result = self.apply_action(
                db,
                game_id=game_id,
                team_id=team_id,
                action_name="geohunter.question.answer",
                object_id=poi_id,
                points_awarded=0,
                allow_repeat=False,
                metadata={"correct": False, "answer": submitted},
                success_message_key="geohunter.answer.incorrect",
                already_message_key="geohunter.answer.alreadySubmitted",
            )
            return GeoHunterAnswerOutcome(
                success=result.success,
                message_key=result.message_key,
                action_id=result.action_id,
                points_awarded=result.points_awarded,
                state_version=result.state_version,
                correct=False,
                score=current_score,
                retry_available_in_seconds=0,
            )

        result: GameActionResult = self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="geohunter.question.answer",
            object_id=poi_id,
            points_awarded=server_points,
            allow_repeat=False,
            metadata={"correct": True, "answer": submitted},
            success_message_key="geohunter.answer.correct",
            already_message_key="geohunter.answer.alreadySubmitted",
        )
        updated_score = self._team_score(db, game_id, team_id)
        return GeoHunterAnswerOutcome(
            success=result.success,
            message_key=result.message_key,
            action_id=result.action_id,
            points_awarded=result.points_awarded,
            state_version=result.state_version,
            correct=True,
            score=updated_score,
            retry_available_in_seconds=0,
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
