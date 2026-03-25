from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import CurrentLocale, CurrentPrincipal, DbSession
from app.modules.base import ApiModule
from app.modules.shared import ACCESS_ADMIN_LABEL, ACCESS_BOTH_LABEL, SharedModuleBase
from app.repositories.market_crash_repository import MarketCrashRepository
from app.services.market_crash_service import MarketCrashService
from app.services.ws_client import WsEventPublisher


class TeamBootstrapResponse(BaseModel):
    state: Dict[str, Any]


class AdminOverviewResponse(BaseModel):
    overview: Dict[str, Any]


class TeamLocationUpdateRequest(BaseModel):
    latitude: float
    longitude: float


class TeamLocationUpdateResponse(BaseModel):
    success: bool
    message_key: str
    location: Dict[str, Any]
    nearby_points: list[Dict[str, Any]]
    nearby_point_ids: list[str]


class ExecuteTradeRequest(BaseModel):
    point_id: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=64)
    side: str = Field(min_length=3, max_length=4)
    quantity: int = Field(default=1, ge=1, le=100000)


class TradeResponse(BaseModel):
    success: bool
    message_key: str
    trade_id: str
    state_version: int
    cash: int
    score: int
    inventory: Dict[str, int]
    nearby_points: list[Dict[str, Any]]
    nearby_point_ids: list[str]
    trade: Dict[str, Any]


class MarketCrashResourcePayload(BaseModel):
    name: str = Field(min_length=2, max_length=32)
    default_price: int = Field(default=25, ge=1, le=100000)


class MarketCrashResourceUpdatePayload(BaseModel):
    default_price: int = Field(default=25, ge=1, le=100000)


class PointResourceSettingPayload(BaseModel):
    resource_id: str = Field(min_length=1, max_length=64)
    buy_price: int = Field(ge=1, le=100000)
    sell_price: int = Field(ge=1, le=100000)
    tick_seconds: int = Field(default=5, ge=1, le=86400)
    fluctuation_percent: float = Field(default=10.0, ge=0.1, le=10.0)


class MarketCrashPointPayload(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    latitude: float
    longitude: float
    radius_meters: int = Field(default=25, ge=5, le=5000)
    marker_color: str = Field(default="#2563eb", min_length=7, max_length=7)
    resources: list[PointResourceSettingPayload] = Field(default_factory=list)


class MarketCrashAdminDataResponse(BaseModel):
    resources: list[Dict[str, Any]]
    points: list[Dict[str, Any]]


class MarketCrashModule(ApiModule, SharedModuleBase):
    name = "market-crash"

    def __init__(self, ws_publisher: WsEventPublisher) -> None:
        """Initialize Market Crash module with domain service and repository."""
        SharedModuleBase.__init__(self, game_type="market_crash", ws_publisher=ws_publisher)
        self._service = MarketCrashService()
        self._repository = MarketCrashRepository()

    def build_router(self) -> APIRouter:
        """Build Market Crash routes for bootstrap, admin config, and team actions."""
        router = APIRouter(prefix="/market-crash", tags=["market-crash"])

        @router.get(
            "/{game_id}/teams/{team_id}/bootstrap",
            response_model=TeamBootstrapResponse,
            summary=f"{ACCESS_BOTH_LABEL} Team bootstrap",
        )
        def team_bootstrap(game_id: str, team_id: str, principal: CurrentPrincipal, db: DbSession) -> TeamBootstrapResponse:
            """Return team runtime bootstrap state for Market Crash gameplay."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)
            return TeamBootstrapResponse(state=self._service.get_team_bootstrap(db, game_id, team_id))

        @router.get(
            "/{game_id}/overview",
            response_model=AdminOverviewResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Admin overview",
        )
        def overview(game_id: str, principal: CurrentPrincipal, db: DbSession) -> AdminOverviewResponse:
            """Return Market Crash admin overview (teams, points, recent actions)."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            return AdminOverviewResponse(overview=self._service.get_admin_overview(db, game_id))

        @router.get(
            "/{game_id}/admin/data",
            response_model=MarketCrashAdminDataResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Market crash admin data",
        )
        def get_admin_data(game_id: str, principal: CurrentPrincipal, db: DbSession) -> MarketCrashAdminDataResponse:
            """Return admin configuration dataset (resources + points)."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)
            resources, points = self._load_admin_data(db, game_id)
            return MarketCrashAdminDataResponse(resources=resources, points=points)

        @router.post(
            "/{game_id}/resources",
            response_model=MarketCrashAdminDataResponse,
            status_code=status.HTTP_201_CREATED,
            summary=f"{ACCESS_ADMIN_LABEL} Create market crash resource",
        )
        def create_resource(game_id: str, body: MarketCrashResourcePayload, principal: CurrentPrincipal, db: DbSession) -> MarketCrashAdminDataResponse:
            """Create market resource and return refreshed admin configuration snapshot."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            name = str(body.name or "").strip().lower()
            if not name:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.resource.invalidName")

            existing = self._repository.get_resource_by_game_id_and_name(db, game_id, name)
            if existing is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="market_crash.resource.exists")

            try:
                self._repository.create_resource_without_commit(
                    db,
                    {"game_id": game_id, "name": name, "default_price": int(body.default_price)},
                )
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.resource.createFailed") from error

            resources, points = self._load_admin_data(db, game_id)
            return MarketCrashAdminDataResponse(resources=resources, points=points)

        @router.put(
            "/{game_id}/resources/{resource_id}",
            response_model=MarketCrashAdminDataResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update market crash resource",
        )
        def update_resource(
            game_id: str,
            resource_id: str,
            body: MarketCrashResourceUpdatePayload,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> MarketCrashAdminDataResponse:
            """Update resource default pricing and return refreshed admin dataset."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            existing = self._repository.get_resource_by_game_id_and_resource_id(db, game_id, resource_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="market_crash.resource.notFound")

            try:
                self._repository.update_resource_without_commit(db, game_id, resource_id, {"default_price": int(body.default_price)})
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.resource.updateFailed") from error

            resources, points = self._load_admin_data(db, game_id)
            return MarketCrashAdminDataResponse(resources=resources, points=points)

        @router.delete(
            "/{game_id}/resources/{resource_id}",
            response_model=MarketCrashAdminDataResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Delete market crash resource",
        )
        def delete_resource(game_id: str, resource_id: str, principal: CurrentPrincipal, db: DbSession) -> MarketCrashAdminDataResponse:
            """Delete resource and return refreshed resources/points configuration."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            existing = self._repository.get_resource_by_game_id_and_resource_id(db, game_id, resource_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="market_crash.resource.notFound")

            try:
                self._repository.delete_resource_without_commit(db, game_id, resource_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.resource.deleteFailed") from error

            resources, points = self._load_admin_data(db, game_id)
            return MarketCrashAdminDataResponse(resources=resources, points=points)

        @router.post(
            "/{game_id}/points",
            response_model=MarketCrashAdminDataResponse,
            status_code=status.HTTP_201_CREATED,
            summary=f"{ACCESS_ADMIN_LABEL} Create market crash point",
        )
        def create_point(game_id: str, body: MarketCrashPointPayload, principal: CurrentPrincipal, db: DbSession) -> MarketCrashAdminDataResponse:
            """Create market point with resource settings and return refreshed admin data."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            point_values, resource_rows = self._validate_point_payload(db, game_id, body)
            point_values["game_id"] = game_id

            try:
                point_id = self._repository.create_point_without_commit(db, point_values)
                self._repository.replace_point_resources_without_commit(db, point_id, resource_rows)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.point.createFailed") from error

            resources, points = self._load_admin_data(db, game_id)
            return MarketCrashAdminDataResponse(resources=resources, points=points)

        @router.put(
            "/{game_id}/points/{point_id}",
            response_model=MarketCrashAdminDataResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Update market crash point",
        )
        def update_point(
            game_id: str,
            point_id: str,
            body: MarketCrashPointPayload,
            principal: CurrentPrincipal,
            db: DbSession,
        ) -> MarketCrashAdminDataResponse:
            """Update market point and attached pricing settings."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            existing = self._repository.get_point_by_game_id_and_point_id(db, game_id, point_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="market_crash.point.notFound")

            point_values, resource_rows = self._validate_point_payload(db, game_id, body)

            try:
                self._repository.update_point_without_commit(db, game_id, point_id, point_values)
                self._repository.replace_point_resources_without_commit(db, point_id, resource_rows)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.point.updateFailed") from error

            resources, points = self._load_admin_data(db, game_id)
            return MarketCrashAdminDataResponse(resources=resources, points=points)

        @router.delete(
            "/{game_id}/points/{point_id}",
            response_model=MarketCrashAdminDataResponse,
            summary=f"{ACCESS_ADMIN_LABEL} Delete market crash point",
        )
        def delete_point(game_id: str, point_id: str, principal: CurrentPrincipal, db: DbSession) -> MarketCrashAdminDataResponse:
            """Delete market point and return refreshed admin configuration."""
            self._require_game(db, game_id)
            self._require_user_manage_access(db, game_id, principal)

            existing = self._repository.get_point_by_game_id_and_point_id(db, game_id, point_id)
            if existing is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="market_crash.point.notFound")

            try:
                self._repository.delete_point_without_commit(db, game_id, point_id)
                self._repository.commit_changes(db)
            except Exception as error:
                self._repository.rollback_on_error(db, error)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.point.deleteFailed") from error

            resources, points = self._load_admin_data(db, game_id)
            return MarketCrashAdminDataResponse(resources=resources, points=points)

        @router.post(
            "/{game_id}/teams/{team_id}/location/update",
            response_model=TeamLocationUpdateResponse,
            summary=f"{ACCESS_BOTH_LABEL} Update team location",
        )
        def update_location(
            game_id: str,
            team_id: str,
            body: TeamLocationUpdateRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> TeamLocationUpdateResponse:
            """Update team location and publish throttled nearby-point/location events."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)

            try:
                location = self._service.update_team_location(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    latitude=float(body.latitude),
                    longitude=float(body.longitude),
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

            nearby_points = self._service.get_nearby_points_for_team(db, game_id, team_id)
            nearby_point_ids = [str(point.get("id") or "") for point in nearby_points]
            should_publish = self._service.should_publish_location_event(db, game_id=game_id, team_id=team_id, min_interval_seconds=10)

            if should_publish:
                self._ws_publisher.publish(
                    "admin.market_crash.team.location.updated",
                    {
                        "game_id": game_id,
                        "team_id": team_id,
                        "lat": location.get("lat"),
                        "lon": location.get("lon"),
                        "updated_at": location.get("updated_at"),
                    },
                    channels=[f"channel:{game_id}:admin"],
                )
                self._ws_publisher.publish(
                    "team.market_crash.nearby_points.updated",
                    {
                        "game_id": game_id,
                        "team_id": team_id,
                        "nearby_point_ids": nearby_point_ids,
                        "nearby_points": nearby_points,
                    },
                    channels=[f"channel:{game_id}:{team_id}"],
                )

            return TeamLocationUpdateResponse(
                success=True,
                message_key=self._localize_message_key("market_crash.location.updated", locale),
                location=location,
                nearby_points=nearby_points,
                nearby_point_ids=nearby_point_ids,
            )

        @router.post(
            "/{game_id}/teams/{team_id}/trade/execute",
            response_model=TradeResponse,
            summary=f"{ACCESS_BOTH_LABEL} Execute trade",
        )
        def execute_trade(
            game_id: str,
            team_id: str,
            body: ExecuteTradeRequest,
            principal: CurrentPrincipal,
            db: DbSession,
            locale: CurrentLocale,
        ) -> TradeResponse:
            """Execute buy/sell trade and emit realtime score/inventory/trade updates."""
            self._require_game(db, game_id)
            self._require_team_self_or_manage_access(db, game_id, team_id, principal)

            try:
                result = self._service.execute_trade(
                    db,
                    game_id=game_id,
                    team_id=team_id,
                    point_id=str(body.point_id or "").strip(),
                    resource_id=str(body.resource_id or "").strip(),
                    side=str(body.side or "").strip(),
                    quantity=int(body.quantity),
                )
            except ValueError as error:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

            self._ws_publisher.publish(
                "team.market_crash.self.updated",
                {
                    "game_id": game_id,
                    "team_id": team_id,
                    "score": int(result.get("score") or 0),
                    "cash": int(result.get("cash") or 0),
                    "inventory": result.get("inventory") or {},
                    "trade": result.get("trade") or {},
                },
                channels=[f"channel:{game_id}:{team_id}"],
            )
            self._ws_publisher.publish(
                "team.market_crash.nearby_points.updated",
                {
                    "game_id": game_id,
                    "team_id": team_id,
                    "nearby_point_ids": result.get("nearby_point_ids") or [],
                    "nearby_points": result.get("nearby_points") or [],
                },
                channels=[f"channel:{game_id}:{team_id}"],
            )

            score_payload = {
                "game_id": game_id,
                "team_id": team_id,
                "score": int(result.get("score") or 0),
                "cash": int(result.get("cash") or 0),
            }
            self._ws_publisher.publish("game.market_crash.team.score", score_payload, channels=[f"channel:{game_id}"])
            self._ws_publisher.publish("admin.market_crash.team.score", score_payload, channels=[f"channel:{game_id}:admin"])

            self._ws_publisher.publish(
                "admin.market_crash.trade.executed",
                {
                    "game_id": game_id,
                    "team_id": team_id,
                    "trade_id": str(result.get("trade_id") or ""),
                    "trade": result.get("trade") or {},
                    "cash": int(result.get("cash") or 0),
                    "score": int(result.get("score") or 0),
                    "inventory": result.get("inventory") or {},
                },
                channels=[f"channel:{game_id}:admin"],
            )

            return TradeResponse(
                success=True,
                message_key=self._localize_message_key(str(result.get("message_key") or "market_crash.trade.executed"), locale),
                trade_id=str(result.get("trade_id") or ""),
                state_version=int(result.get("state_version") or 0),
                cash=int(result.get("cash") or 0),
                score=int(result.get("score") or 0),
                inventory={str(k): int(v) for k, v in dict(result.get("inventory") or {}).items()},
                nearby_points=list(result.get("nearby_points") or []),
                nearby_point_ids=[str(value) for value in list(result.get("nearby_point_ids") or [])],
                trade=dict(result.get("trade") or {}),
            )

        return router

    def _load_admin_data(self, db: DbSession, game_id: str) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
        """Load normalized resources and point configurations for admin screens."""
        resources_raw = self._repository.fetch_resources_by_game_id(db, game_id)
        resources = [
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or ""),
                "default_price": int(row.get("default_price") or 1),
            }
            for row in resources_raw
        ]
        resource_name_by_id = {resource["id"]: resource["name"] for resource in resources}

        points_raw = self._repository.fetch_points_by_game_id(db, game_id)
        points: list[Dict[str, Any]] = []
        for row in points_raw:
            point_id = str(row.get("id") or "")
            point_resources = self._repository.fetch_point_resources_by_point_id(db, point_id)
            resource_settings = []
            for item in point_resources:
                resource_id = str(item.get("resource_id") or "")
                resource_settings.append(
                    {
                        "resource_id": resource_id,
                        "resource_name": resource_name_by_id.get(resource_id, ""),
                        "buy_price": int(item.get("buy_price") or 1),
                        "sell_price": int(item.get("sell_price") or 1),
                        "tick_seconds": int(item.get("tick_seconds") or 5),
                        "fluctuation_percent": float(item.get("fluctuation_percent") or 10.0),
                    }
                )

            points.append(
                {
                    "id": point_id,
                    "title": str(row.get("title") or ""),
                    "latitude": float(row.get("latitude") or 0),
                    "longitude": float(row.get("longitude") or 0),
                    "radius_meters": int(row.get("radius_meters") or 25),
                    "marker_color": str(row.get("marker_color") or "#2563eb"),
                    "resource_settings": resource_settings,
                }
            )

        return resources, points

    def _validate_point_payload(
        self,
        db: DbSession,
        game_id: str,
        body: MarketCrashPointPayload,
    ) -> tuple[Dict[str, Any], list[Dict[str, Any]]]:
        """Validate point payload and map resource settings to repository rows."""
        title = str(body.title or "").strip()
        marker_color = str(body.marker_color or "#2563eb").strip().lower()
        if not title:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.point.invalidTitle")
        if len(marker_color) != 7 or not marker_color.startswith("#"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.point.invalidColor")

        resources_by_id = {
            str(resource.get("id") or ""): resource
            for resource in self._repository.fetch_resources_by_game_id(db, game_id)
        }

        rows: list[Dict[str, Any]] = []
        for setting in body.resources:
            resource_id = str(setting.resource_id or "").strip()
            if not resource_id or resource_id not in resources_by_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.point.invalidResource")

            rows.append(
                {
                    "resource_id": resource_id,
                    "buy_price": int(setting.buy_price),
                    "sell_price": int(setting.sell_price),
                    "tick_seconds": int(setting.tick_seconds),
                    "fluctuation_percent": min(10.0, float(setting.fluctuation_percent)),
                }
            )

        if not rows:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="market_crash.point.resourcesRequired")

        point_values = {
            "title": title,
            "latitude": float(body.latitude),
            "longitude": float(body.longitude),
            "radius_meters": int(body.radius_meters),
            "marker_color": marker_color,
        }

        return point_values, rows
