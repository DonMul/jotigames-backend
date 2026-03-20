from app.dependencies import DbSession
from app.repositories.pandemic_response_repository import PandemicResponseRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class PandemicResponseService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("pandemic_response", repository=PandemicResponseRepository())

    def collect_pickup(self, db: DbSession, *, game_id: str, team_id: str, pickup_id: str) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="pandemic_response.pickup.collect",
            object_id=pickup_id,
            points_awarded=0,
            allow_repeat=False,
            success_message_key="pandemic_response.pickup.collected",
            already_message_key="pandemic_response.pickup.alreadyCollected",
        )

    def resolve_hotspot(self, db: DbSession, *, game_id: str, team_id: str, hotspot_id: str, points: int = 1) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="pandemic_response.hotspot.resolve",
            object_id=hotspot_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="pandemic_response.hotspot.resolved",
            already_message_key="pandemic_response.hotspot.alreadyResolved",
        )
