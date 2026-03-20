from app.dependencies import DbSession
from app.repositories.checkpoint_heist_repository import CheckpointHeistRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class CheckpointHeistService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("checkpoint_heist", repository=CheckpointHeistRepository())

    def capture_checkpoint(self, db: DbSession, *, game_id: str, team_id: str, checkpoint_id: str, points: int = 1) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="checkpoint_heist.capture.confirm",
            object_id=checkpoint_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="checkpoint_heist.capture.recorded",
            already_message_key="checkpoint_heist.capture.alreadyCaptured",
        )
