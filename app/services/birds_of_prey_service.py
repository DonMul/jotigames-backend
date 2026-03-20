from app.dependencies import DbSession
from app.repositories.birds_of_prey_repository import BirdsOfPreyRepository
from app.services.game_logic_service import GameActionResult, GameLogicService


class BirdsOfPreyService(GameLogicService):
    def __init__(self) -> None:
        super().__init__("birds_of_prey", repository=BirdsOfPreyRepository())

    def drop_egg(self, db: DbSession, *, game_id: str, team_id: str, egg_id: str) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="birds_of_prey.egg.drop",
            object_id=egg_id,
            points_awarded=0,
            allow_repeat=True,
            success_message_key="birds_of_prey.egg.dropped",
            already_message_key="birds_of_prey.egg.dropped",
        )

    def destroy_egg(self, db: DbSession, *, game_id: str, team_id: str, egg_id: str, points: int = 1) -> GameActionResult:
        return self.apply_action(
            db,
            game_id=game_id,
            team_id=team_id,
            action_name="birds_of_prey.egg.destroy",
            object_id=egg_id,
            points_awarded=max(0, int(points)),
            allow_repeat=False,
            success_message_key="birds_of_prey.egg.destroyed",
            already_message_key="birds_of_prey.egg.alreadyDestroyed",
        )
