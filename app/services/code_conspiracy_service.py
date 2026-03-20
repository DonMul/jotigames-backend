from app.dependencies import DbSession
from app.repositories.code_conspiracy_repository import CodeConspiracyRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class CodeConspiracyService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("code_conspiracy", repository=CodeConspiracyRepository())

    def submit_code(self, db: DbSession, *, game_id: str, team_id: str, target_team_id: str, code_value: str, points_delta: int = 0) -> GameActionResult:
        object_id = f"{target_team_id}:{code_value.strip().lower()}"
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="code_conspiracy.code.submit",
            object_id=object_id,
            points_awarded=int(points_delta),
            allow_repeat=True,
            metadata={"target_team_id": target_team_id},
            success_message_key="code_conspiracy.code.submitted",
            already_message_key="code_conspiracy.code.submitted",
        )
