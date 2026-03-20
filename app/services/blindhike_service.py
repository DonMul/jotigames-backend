from app.dependencies import DbSession
from app.repositories.blindhike_repository import BlindHikeRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class BlindHikeService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("blindhike", repository=BlindHikeRepository())

    def add_marker(self, db: DbSession, *, game_id: str, team_id: str, marker_id: str) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="blindhike.marker.add",
            object_id=marker_id,
            points_awarded=0,
            allow_repeat=False,
            success_message_key="blindhike.marker.added",
            already_message_key="blindhike.marker.alreadyAdded",
        )
