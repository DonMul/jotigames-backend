from app.dependencies import DbSession
from app.repositories.territory_control_repository import TerritoryControlRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class TerritoryControlService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("territory_control", repository=TerritoryControlRepository())

    def claim_zone(self, db: DbSession, *, game_id: str, team_id: str, zone_id: str, points: int = 1) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="territory_control.poi.claim",
            object_id=zone_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="territory_control.claim.recorded",
            already_message_key="territory_control.claim.alreadyOwned",
        )
