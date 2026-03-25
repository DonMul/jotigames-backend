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
