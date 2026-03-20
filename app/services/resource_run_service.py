from app.dependencies import DbSession
from app.repositories.resource_run_repository import ResourceRunRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class ResourceRunService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("resource_run", repository=ResourceRunRepository())

    def claim_resource(self, db: DbSession, *, game_id: str, team_id: str, node_id: str, points: int = 1) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="resource_run.resource.claim",
            object_id=node_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="resource_run.claim.recorded",
            already_message_key="resource_run.claim.alreadyCollected",
        )
