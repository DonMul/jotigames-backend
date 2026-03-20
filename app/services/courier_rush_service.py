from app.dependencies import DbSession
from app.repositories.courier_rush_repository import CourierRushRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class CourierRushService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("courier_rush", repository=CourierRushRepository())

    def confirm_pickup(self, db: DbSession, *, game_id: str, team_id: str, pickup_id: str) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="courier_rush.pickup.confirm",
            object_id=pickup_id,
            points_awarded=0,
            allow_repeat=False,
            success_message_key="courier_rush.pickup.confirmed",
            already_message_key="courier_rush.pickup.alreadyConfirmed",
        )

    def confirm_dropoff(self, db: DbSession, *, game_id: str, team_id: str, dropoff_id: str, points: int = 1) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="courier_rush.dropoff.confirm",
            object_id=dropoff_id,
            points_awarded=max(0, int(points)),
            allow_repeat=True,
            success_message_key="courier_rush.dropoff.confirmed",
            already_message_key="courier_rush.dropoff.confirmed",
        )
