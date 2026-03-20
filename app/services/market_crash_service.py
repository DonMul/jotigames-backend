from app.dependencies import DbSession
from app.repositories.market_crash_repository import MarketCrashRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class MarketCrashService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("market_crash", repository=MarketCrashRepository())

    def execute_trade(self, db: DbSession, *, game_id: str, team_id: str, trade_id: str, points: int = 0) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="market_crash.trade.execute",
            object_id=trade_id,
            points_awarded=int(points),
            allow_repeat=True,
            success_message_key="market_crash.trade.executed",
            already_message_key="market_crash.trade.executed",
        )
