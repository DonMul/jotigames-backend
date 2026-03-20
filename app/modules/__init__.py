from app.modules.auth import AuthModule
from app.modules.birds_of_prey import BirdsOfPreyModule
from app.modules.blindhike import BlindHikeModule
from app.modules.checkpoint_heist import CheckpointHeistModule
from app.modules.code_conspiracy import CodeConspiracyModule
from app.modules.courier_rush import CourierRushModule
from app.modules.crazy88 import Crazy88Module
from app.modules.echo_hunt import EchoHuntModule
from app.modules.exploding_kittens import ExplodingKittensModule
from app.modules.game import GameModule
from app.modules.geohunter import GeoHunterModule
from app.modules.market_crash import MarketCrashModule
from app.modules.pandemic_response import PandemicResponseModule
from app.modules.resource_run import ResourceRunModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, ACCESS_SUPER_ADMIN_LABEL, ACCESS_TEAM_LABEL
from app.modules.super_admin import SuperAdminModule
from app.modules.system import SystemModule
from app.modules.territory_control import TerritoryControlModule

__all__ = [
	"AuthModule",
	"BirdsOfPreyModule",
	"BlindHikeModule",
	"CheckpointHeistModule",
	"CodeConspiracyModule",
	"CourierRushModule",
	"Crazy88Module",
	"EchoHuntModule",
	"ExplodingKittensModule",
	"GameModule",
	"GeoHunterModule",
	"MarketCrashModule",
	"PandemicResponseModule",
	"ResourceRunModule",
	"SuperAdminModule",
	"SystemModule",
	"TerritoryControlModule",
	"ACCESS_ADMIN_LABEL",
	"ACCESS_BOTH_LABEL",
	"ACCESS_TEAM_LABEL",
	"ACCESS_SUPER_ADMIN_LABEL",
]
