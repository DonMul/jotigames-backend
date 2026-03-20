from app.dependencies import DbSession
from app.repositories.echo_hunt_repository import EchoHuntRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class EchoHuntService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("echo_hunt", repository=EchoHuntRepository())

    def claim_beacon(self, db: DbSession, *, game_id: str, team_id: str, beacon_id: str, points: int = 1) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="echo_hunt.beacon.claim",
            object_id=beacon_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="echo_hunt.beacon.claimed",
            already_message_key="echo_hunt.beacon.alreadyClaimed",
        )
