from typing import Any, Dict

from app.dependencies import DbSession
from app.repositories.geohunter_repository import GeoHunterRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class GeoHunterService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("geohunter", repository=GeoHunterRepository())

    def answer_question(self, db: DbSession, *, game_id: str, team_id: str, poi_id: str, correct: bool) -> GameActionResult:
        points = 1 if correct else 0
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="geohunter.question.answer",
            object_id=poi_id,
            points_awarded=points,
            allow_repeat=False,
            metadata={"correct": bool(correct)},
            success_message_key="geohunter.answer.recorded",
            already_message_key="geohunter.answer.alreadySubmitted",
        )
