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
    """Context object passed to game-type reset handlers."""

    game_id: str
    game_type: str


class BaseGameTypeResetHandler:
    """Base reset handler interface for per-game-type cleanup logic."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Default no-op reset hook for unsupported or stateless game types."""
        return


class GeoHunterResetHandler(BaseGameTypeResetHandler):
    """Reset handler for GeoHunter tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear GeoHunter submissions tied to points in this game."""
        repository.deleteByParentGameId(db, "geo_submission", "point_id", "geo_point", context.game_id)


class BlindHikeResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Blind Hike tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear Blind Hike marker progress for the game."""
        repository.deleteByGameId(db, "blind_hike_marker", context.game_id)


class ResourceRunResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Resource Run tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear Resource Run collections linked to game nodes."""
        repository.deleteByParentGameId(db, "resource_run_collection", "node_id", "resource_run_node", context.game_id)


class TerritoryControlResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Territory Control tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear captures and reset zone state for Territory Control."""
        repository.deleteByParentGameId(db, "territory_capture", "zone_id", "territory_zone", context.game_id)
        repository.resetTerritoryZonesByGameId(db, context.game_id)


class MarketCrashResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Market Crash tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear Market Crash trades and per-team inventories for a fresh round."""
        repository.deleteByParentGameId(db, "market_crash_trade", "point_id", "market_crash_point", context.game_id)
        repository.deleteByTeamGameId(db, "market_crash_inventory", "team_id", context.game_id)


class Crazy88ResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Crazy88 tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear Crazy88 task submissions linked to game tasks."""
        repository.deleteByParentGameId(db, "crazy_88_submission", "task_id", "crazy_88_task", context.game_id)


class CourierRushResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Courier Rush tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear Courier Rush task state including pickup options and tasks."""
        repository.deleteByParentGameId(
            db,
            "courier_rush_task_pickup_option",
            "task_id",
            "courier_rush_task",
            context.game_id,
        )
        repository.deleteByGameId(db, "courier_rush_task", context.game_id)


class EchoHuntResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Echo Hunt tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear Echo Hunt discoveries associated with configured beacons."""
        repository.deleteByParentGameId(db, "echo_hunt_discovery", "beacon_id", "echo_hunt_beacon", context.game_id)


class CheckpointHeistResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Checkpoint Heist tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear Checkpoint Heist captures/progress for replayable sessions."""
        repository.deleteByParentGameId(
            db,
            "checkpoint_heist_capture",
            "checkpoint_id",
            "checkpoint_heist_checkpoint",
            context.game_id,
        )
        repository.deleteByGameId(db, "checkpoint_heist_progress", context.game_id)


class PandemicResponseResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Pandemic Response tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear Pandemic Response claims/resources/hotspots/pickups for reset."""
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
    """Reset handler for Birds of Prey tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear Birds of Prey egg state for this game."""
        repository.deleteByGameId(db, "birds_of_prey_egg", context.game_id)


class CodeConspiracyResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Code Conspiracy tables."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """Clear Code Conspiracy submissions and winner assignment state."""
        repository.deleteByGameId(db, "code_conspiracy_submission", context.game_id)
        repository.deleteByGameId(db, "code_conspiracy_verification", context.game_id)
        repository.deleteByGameId(db, "code_conspiracy_team_code", context.game_id)
        repository.resetCodeConspiracyWinnerByGameId(db, context.game_id)


class ExplodingKittensResetHandler(BaseGameTypeResetHandler):
    """Reset handler for Exploding Kittens specific extras."""

    def reset(self, db: DbSession, repository: GameResetRepository, context: GameResetContext) -> None:
        """No-op: Exploding Kittens reset is handled by generic card resets."""
        return


class GameResetService:
    """Coordinates full game-reset flows across all supported game types."""

    def __init__(self) -> None:
        """Initialize reset service with repository and game-type handler map."""
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
        """Reset generic and game-specific runtime state, then commit transaction.

        Flow:
        1. Reset shared team/card/message usage state.
        2. Run game-type specific cleanup handler.
        3. Commit all reset operations atomically.
        """
        context = GameResetContext(game_id=game_id, game_type=game_type)

        self._repository.resetTeamsByGameId(db, game_id, game_type in _GEO_RESET_GAME_TYPES)
        self._repository.resetCardsByGameId(db, game_id)
        self._repository.deleteByGameId(db, "card_action", game_id)
        self._repository.deleteByGameId(db, "team_message", game_id)
        self._repository.deleteCardUsageByGameId(db, game_id)

        handler = self._handlers.get(game_type, self._default_handler)
        handler.reset(db, self._repository, context)

        db.commit()
