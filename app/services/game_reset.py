from dataclasses import dataclass
from typing import Dict

from app.dependencies import DbSession
from app.repositories.game_reset_repository import GameResetRepository

_GEO_RESET_GAME_TYPES = {
    "geohunter",
    "resource_run",
    "territory_control",
    "market_crash",
    "crazy_88",
    "courier_rush",
    "echo_hunt",
    "checkpoint_heist",
    "birds_of_prey",
    "code_conspiracy",
}


@dataclass
class GameResetContext:
    game_id: str
    game_type: str


class BaseGameTypeResetHandler:
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        return


class GeoHunterResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByParentGameId(db, "geo_submission", "point_id", "geo_point", context.game_id)


class BlindHikeResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByGameId(db, "blind_hike_marker", context.game_id)


class ResourceRunResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByParentGameId(db, "resource_run_collection", "node_id", "resource_run_node", context.game_id)


class TerritoryControlResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByParentGameId(db, "territory_capture", "zone_id", "territory_zone", context.game_id)
        repository.resetTerritoryZonesByGameId(db, context.game_id)


class MarketCrashResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByParentGameId(db, "market_crash_trade", "point_id", "market_crash_point", context.game_id)
        repository.deleteByTeamGameId(db, "market_crash_inventory", "team_id", context.game_id)


class Crazy88ResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByParentGameId(db, "crazy_88_submission", "task_id", "crazy_88_task", context.game_id)


class CourierRushResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByParentGameId(
            db,
            "courier_rush_task_pickup_option",
            "task_id",
            "courier_rush_task",
            context.game_id,
        )
        repository.deleteByGameId(db, "courier_rush_task", context.game_id)


class EchoHuntResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByParentGameId(db, "echo_hunt_discovery", "beacon_id", "echo_hunt_beacon", context.game_id)


class CheckpointHeistResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByParentGameId(
            db,
            "checkpoint_heist_capture",
            "checkpoint_id",
            "checkpoint_heist_checkpoint",
            context.game_id,
        )
        repository.deleteByGameId(db, "checkpoint_heist_progress", context.game_id)


class PandemicResponseResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByParentGameId(
            db,
            "pandemic_response_claim",
            "hotspot_id",
            "pandemic_response_hotspot",
            context.game_id,
        )
        repository.deleteByGameId(db, "pandemic_response_hotspot", context.game_id)
        repository.deleteByTeamGameId(db, "pandemic_response_team_resource", "team_id", context.game_id)
        repository.deleteByGameId(db, "pandemic_response_pickup_point", context.game_id)


class BirdsOfPreyResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByGameId(db, "birds_of_prey_egg", context.game_id)


class CodeConspiracyResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        repository.deleteByGameId(db, "code_conspiracy_submission", context.game_id)
        repository.deleteByGameId(db, "code_conspiracy_verification", context.game_id)
        repository.deleteByGameId(db, "code_conspiracy_team_code", context.game_id)
        repository.resetCodeConspiracyWinnerByGameId(db, context.game_id)


class ExplodingKittensResetHandler(BaseGameTypeResetHandler):
    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        return


class GameResetService:
    def __init__(self) -> None:
        self._repository = GameResetRepository()
        self._default_handler = BaseGameTypeResetHandler()
        self._handlers: Dict[str, BaseGameTypeResetHandler] = {
            "exploding_kittens": ExplodingKittensResetHandler(),
            "geohunter": GeoHunterResetHandler(),
            "blindhike": BlindHikeResetHandler(),
            "resource_run": ResourceRunResetHandler(),
            "territory_control": TerritoryControlResetHandler(),
            "market_crash": MarketCrashResetHandler(),
            "crazy_88": Crazy88ResetHandler(),
            "courier_rush": CourierRushResetHandler(),
            "echo_hunt": EchoHuntResetHandler(),
            "checkpoint_heist": CheckpointHeistResetHandler(),
            "pandemic_response": PandemicResponseResetHandler(),
            "birds_of_prey": BirdsOfPreyResetHandler(),
            "code_conspiracy": CodeConspiracyResetHandler(),
        }

    def resetGameByIdAndType(self, db: DbSession, game_id: str, game_type: str) -> None:
        context = GameResetContext(game_id=game_id, game_type=game_type)

        self._repository.resetTeamsByGameId(db, game_id, game_type in _GEO_RESET_GAME_TYPES)
        self._repository.resetCardsByGameId(db, game_id)
        self._repository.deleteByGameId(db, "card_action", game_id)
        self._repository.deleteByGameId(db, "team_message", game_id)
        self._repository.deleteCardUsageByGameId(db, game_id)

        handler = self._handlers.get(game_type, self._default_handler)
        handler.reset(db, self._repository, context)

        db.commit()
