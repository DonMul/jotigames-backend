from datetime import UTC, datetime
from math import atan2, cos, radians, sin, sqrt
from typing import Any, Dict, Optional

from app.dependencies import DbSession
from app.repositories.market_crash_repository import MarketCrashRepository
from app.services.game_logic_service import GameLogicService


class MarketCrashService(GameLogicService):
    _EARTH_RADIUS_METERS = 6371000.0

    def __init__(self) -> None:
        """Initialize Market Crash service with shared game-logic repository wiring."""
        super().__init__("market_crash", repository=MarketCrashRepository())

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        """Best-effort float conversion returning `None` for invalid/NaN values."""
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if numeric == numeric else None

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Best-effort integer conversion with explicit default fallback."""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _distance_meters(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
        """Compute great-circle distance in meters using Haversine formula."""
        lat1 = radians(lat_a)
        lon1 = radians(lon_a)
        lat2 = radians(lat_b)
        lon2 = radians(lon_b)

        dlat = lat2 - lat1
        dlon = lon2 - lon1
        hav = (sin(dlat / 2) ** 2) + (cos(lat1) * cos(lat2) * (sin(dlon / 2) ** 2))
        c = 2 * atan2(sqrt(hav), sqrt(1 - hav))
        return MarketCrashService._EARTH_RADIUS_METERS * c

    @staticmethod
    def _parse_timestamp(raw: Any) -> Optional[datetime]:
        """Parse timestamps from datetime or string input into UTC-aware datetime."""
        if isinstance(raw, datetime):
            return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)

        value = str(raw or "").strip()
        if not value:
            return None

        normalized = value.replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        if "+" not in normalized and "T" in normalized:
            normalized = f"{normalized}+00:00"

        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        return parsed.astimezone(UTC)

    def should_publish_location_event(self, db: DbSession, *, game_id: str, team_id: str, min_interval_seconds: int = 10) -> bool:
        """Throttle team-location WS events using per-team publish timestamps."""
        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)
        entry = self._team_state_entry(game_state, team_id)
        now = datetime.now(UTC)

        previous_at = self._parse_timestamp(entry.get("last_location_publish_at"))
        if previous_at is not None and (now - previous_at).total_seconds() < float(min_interval_seconds):
            return False

        entry["last_location_publish_at"] = now.isoformat()
        self._repository.update_game_settings_without_commit(db, game_id, settings)
        self._repository.commit_changes(db)
        return True

    def _load_resource_maps(self, db: DbSession, game_id: str) -> tuple[list[Dict[str, Any]], Dict[str, str], Dict[str, Dict[str, Any]]]:
        """Load resources and build id->name/id->resource lookup maps."""
        resources = self._repository.fetch_resources_by_game_id(db, game_id)
        resource_name_by_id: Dict[str, str] = {}
        resource_by_id: Dict[str, Dict[str, Any]] = {}

        for row in resources:
            resource_id = str(row.get("id") or "").strip()
            if not resource_id:
                continue
            normalized = {
                "id": resource_id,
                "name": str(row.get("name") or "").strip(),
                "default_price": self._safe_int(row.get("default_price"), 1),
            }
            resource_name_by_id[resource_id] = normalized["name"]
            resource_by_id[resource_id] = normalized

        return resources, resource_name_by_id, resource_by_id

    def _point_rows_with_resources(
        self,
        db: DbSession,
        game_id: str,
        *,
        resource_name_by_id: Dict[str, str],
    ) -> list[Dict[str, Any]]:
        """Load points enriched with per-resource pricing and settings maps."""
        points_raw = self._repository.fetch_points_by_game_id(db, game_id)
        points: list[Dict[str, Any]] = []

        for row in points_raw:
            point_id = str(row.get("id") or "")
            point_resources = self._repository.fetch_point_resources_by_point_id(db, point_id)

            resource_settings: list[Dict[str, Any]] = []
            buy_prices_by_resource_id: Dict[str, int] = {}
            sell_prices_by_resource_id: Dict[str, int] = {}
            buy_prices_by_name: Dict[str, int] = {}
            sell_prices_by_name: Dict[str, int] = {}

            for item in point_resources:
                resource_id = str(item.get("resource_id") or "").strip()
                if not resource_id:
                    continue

                buy_price = self._safe_int(item.get("buy_price"), 0)
                sell_price = self._safe_int(item.get("sell_price"), 0)
                tick_seconds = max(1, self._safe_int(item.get("tick_seconds"), 5))
                fluctuation_percent = float(item.get("fluctuation_percent") or 10.0)
                resource_name = resource_name_by_id.get(resource_id, resource_id)

                entry = {
                    "resource_id": resource_id,
                    "resource_name": resource_name,
                    "buy_price": buy_price,
                    "sell_price": sell_price,
                    "tick_seconds": tick_seconds,
                    "fluctuation_percent": fluctuation_percent,
                }
                resource_settings.append(entry)
                buy_prices_by_resource_id[resource_id] = buy_price
                sell_prices_by_resource_id[resource_id] = sell_price
                buy_prices_by_name[resource_name] = buy_price
                sell_prices_by_name[resource_name] = sell_price

            points.append(
                {
                    "id": point_id,
                    "title": str(row.get("title") or ""),
                    "latitude": float(row.get("latitude") or 0),
                    "longitude": float(row.get("longitude") or 0),
                    "radius_meters": self._safe_int(row.get("radius_meters"), 25),
                    "marker_color": str(row.get("marker_color") or "#2563eb"),
                    "resource_settings": resource_settings,
                    "buy_prices_by_resource_id": buy_prices_by_resource_id,
                    "sell_prices_by_resource_id": sell_prices_by_resource_id,
                    "buy_prices": buy_prices_by_name,
                    "sell_prices": sell_prices_by_name,
                }
            )

        return points

    def _with_range_for_team(
        self,
        points: list[Dict[str, Any]],
        *,
        team_location: Dict[str, Any],
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
        """Annotate points with distance/in-range flags and split nearby subset."""
        team_lat = self._safe_float(team_location.get("lat"))
        team_lon = self._safe_float(team_location.get("lon"))

        full: list[Dict[str, Any]] = []
        nearby: list[Dict[str, Any]] = []

        for point in points:
            latitude = self._safe_float(point.get("latitude"))
            longitude = self._safe_float(point.get("longitude"))
            radius_meters = self._safe_int(point.get("radius_meters"), 25)

            distance = None
            in_range = False
            if team_lat is not None and team_lon is not None and latitude is not None and longitude is not None:
                distance = self._distance_meters(team_lat, team_lon, latitude, longitude)
                in_range = distance <= float(radius_meters)

            row = {
                **point,
                "distance_meters": distance,
                "in_range": in_range,
            }
            full.append(row)
            if in_range:
                nearby.append(row)

        return full, nearby

    def _leaderboard_rows(
        self,
        db: DbSession,
        game_id: str,
        *,
        resource_name_by_id: Dict[str, str],
    ) -> list[Dict[str, Any]]:
        """Build sorted leaderboard rows with cash, score, inventory, and location."""
        teams = self._repository.fetch_teams_by_game_id(db, game_id)
        team_locations = self._repository.fetch_team_locations_by_game_id(db, game_id)
        starting_cash = self._repository.get_starting_cash(db, game_id)

        rows: list[Dict[str, Any]] = []
        for team in teams:
            team_id = str(team.get("id") or "")
            if not team_id:
                continue

            cash = self._repository.calculate_team_cash(db, team_id, starting_cash)
            inventory = self._repository.build_inventory_map(db, team_id, resource_name_by_id)
            location = team_locations.get(team_id) or {}

            rows.append(
                {
                    "team_id": team_id,
                    "name": str(team.get("name") or ""),
                    "logo_path": str(team.get("logo_path") or ""),
                    "score": int(team.get("geo_score") or cash),
                    "cash": cash,
                    "inventory": inventory,
                    "lat": self._safe_float(location.get("lat")),
                    "lon": self._safe_float(location.get("lon")),
                    "location_updated_at": str(location.get("updated_at") or ""),
                }
            )

        rows.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("name") or "").lower()))
        return rows

    def get_team_bootstrap(self, db: DbSession, game_id: str, team_id: str) -> Dict[str, Any]:
        """Assemble complete team bootstrap payload for Market Crash runtime."""
        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)

        _, resource_name_by_id, _ = self._load_resource_maps(db, game_id)
        points = self._point_rows_with_resources(db, game_id, resource_name_by_id=resource_name_by_id)
        team_location = self._repository.get_team_location(db, game_id, team_id)
        points_with_range, nearby_points = self._with_range_for_team(points, team_location=team_location)

        starting_cash = self._repository.get_starting_cash(db, game_id)
        cash = self._repository.calculate_team_cash(db, team_id, starting_cash)
        inventory = self._repository.build_inventory_map(db, team_id, resource_name_by_id)

        team_state = self._team_state_entry(game_state, team_id)

        return {
            "version": int(game_state.get("version") or 0),
            "team_id": team_id,
            "score": int(cash),
            "cash": int(cash),
            "starting_cash": int(starting_cash),
            "inventory": inventory,
            "actions": int(team_state.get("actions") or 0),
            "last_action_at": team_state.get("last_action_at"),
            "team_location": team_location,
            "points": points_with_range,
            "nearby_points": nearby_points,
            "nearby_point_ids": [str(point.get("id") or "") for point in nearby_points],
            "leaderboard": self._leaderboard_rows(db, game_id, resource_name_by_id=resource_name_by_id),
        }

    def get_admin_overview(self, db: DbSession, game_id: str) -> Dict[str, Any]:
        """Assemble admin overview containing points, teams, and recent actions."""
        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)

        _, resource_name_by_id, _ = self._load_resource_maps(db, game_id)
        points = self._point_rows_with_resources(db, game_id, resource_name_by_id=resource_name_by_id)
        teams = self._leaderboard_rows(db, game_id, resource_name_by_id=resource_name_by_id)

        return {
            "version": int(game_state.get("version") or 0),
            "points": points,
            "teams": teams,
            "recent_actions": list(game_state.get("actions") or [])[-50:],
        }

    def get_nearby_points_for_team(self, db: DbSession, game_id: str, team_id: str) -> list[Dict[str, Any]]:
        """Return currently in-range points for the team based on last location."""
        _, resource_name_by_id, _ = self._load_resource_maps(db, game_id)
        points = self._point_rows_with_resources(db, game_id, resource_name_by_id=resource_name_by_id)
        location = self._repository.get_team_location(db, game_id, team_id)
        _, nearby_points = self._with_range_for_team(points, team_location=location)
        return nearby_points

    def update_team_location(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        latitude: float,
        longitude: float,
    ) -> Dict[str, Any]:
        """Validate and persist team location coordinates."""
        lat = self._safe_float(latitude)
        lon = self._safe_float(longitude)
        if lat is None or lon is None or lat < -90 or lat > 90 or lon < -180 or lon > 180:
            raise ValueError("market_crash.location.invalid")

        self._repository.update_team_location_without_commit(
            db,
            game_id,
            team_id,
            latitude=lat,
            longitude=lon,
        )
        self._repository.commit_changes(db)

        return self._repository.get_team_location(db, game_id, team_id)

    def execute_trade(
        self,
        db: DbSession,
        *,
        game_id: str,
        team_id: str,
        point_id: str,
        resource_id: str,
        side: str,
        quantity: int,
    ) -> Dict[str, Any]:
        """Execute trade transaction with range, inventory, and cash validation.

        The method enforces trade-side validity, in-range checks, available
        cash/inventory constraints, records trade + inventory updates, appends
        action history, increments state version, and returns updated team state
        fragments for API/WS responses.
        """
        normalized_side = str(side or "").strip().lower()
        if normalized_side not in {"buy", "sell"}:
            raise ValueError("market_crash.trade.invalidSide")

        normalized_quantity = max(1, self._safe_int(quantity, 1))
        point = self._repository.get_point_by_game_id_and_point_id(db, game_id, point_id)
        if point is None:
            raise ValueError("market_crash.point.notFound")

        point_resource = self._repository.get_point_resource(db, point_id, resource_id)
        if point_resource is None:
            raise ValueError("market_crash.trade.invalidResource")

        team_location = self._repository.get_team_location(db, game_id, team_id)
        team_lat = self._safe_float(team_location.get("lat"))
        team_lon = self._safe_float(team_location.get("lon"))
        if team_lat is None or team_lon is None:
            raise ValueError("market_crash.location.required")

        point_lat = self._safe_float(point.get("latitude"))
        point_lon = self._safe_float(point.get("longitude"))
        point_radius = self._safe_int(point.get("radius_meters"), 25)
        if point_lat is None or point_lon is None:
            raise ValueError("market_crash.point.invalid")

        distance = self._distance_meters(team_lat, team_lon, point_lat, point_lon)
        if distance > float(point_radius):
            raise ValueError("market_crash.trade.outOfRange")

        buy_price = self._safe_int(point_resource.get("buy_price"), 0)
        sell_price = self._safe_int(point_resource.get("sell_price"), 0)
        unit_price = buy_price if normalized_side == "buy" else sell_price
        if unit_price <= 0:
            raise ValueError("market_crash.trade.invalidPrice")

        total_amount = int(unit_price * normalized_quantity)
        starting_cash = self._repository.get_starting_cash(db, game_id)
        current_cash = self._repository.calculate_team_cash(db, team_id, starting_cash)

        _, resource_name_by_id, _ = self._load_resource_maps(db, game_id)
        resource_name = resource_name_by_id.get(resource_id, resource_id)
        inventory_before = self._repository.build_inventory_map(db, team_id, resource_name_by_id)
        current_quantity = int(inventory_before.get(resource_name, 0))

        if normalized_side == "buy" and current_cash < total_amount:
            raise ValueError("market_crash.trade.insufficientCash")
        if normalized_side == "sell" and current_quantity < normalized_quantity:
            raise ValueError("market_crash.trade.insufficientInventory")

        next_quantity = current_quantity + normalized_quantity if normalized_side == "buy" else current_quantity - normalized_quantity

        trade_id = self._repository.create_trade_without_commit(
            db,
            point_id=point_id,
            team_id=team_id,
            resource_id=resource_id,
            side=normalized_side,
            quantity=normalized_quantity,
            unit_price=unit_price,
            total_amount=total_amount,
        )

        self._repository.upsert_inventory_quantity_without_commit(
            db,
            team_id=team_id,
            resource_id=resource_id,
            quantity=max(0, next_quantity),
        )

        next_cash = current_cash - total_amount if normalized_side == "buy" else current_cash + total_amount
        self._repository.set_team_geo_score_without_commit(db, team_id, max(0, int(next_cash)))

        settings = self._load_game_state(db, game_id)
        game_state = self._game_state_from_settings(settings)
        actions = game_state.get("actions")
        if not isinstance(actions, list):
            actions = []
            game_state["actions"] = actions

        now = datetime.now(UTC).isoformat()
        action_entry = {
            "id": f"{team_id}:market_crash.trade.execute:{trade_id}:{int(datetime.now(UTC).timestamp())}",
            "team_id": team_id,
            "action": "market_crash.trade.execute",
            "object_id": str(trade_id),
            "at": now,
            "metadata": {
                "point_id": point_id,
                "resource_id": resource_id,
                "resource_name": resource_name,
                "side": normalized_side,
                "quantity": normalized_quantity,
                "unit_price": unit_price,
                "total_amount": total_amount,
            },
        }
        actions.append(action_entry)

        team_state = self._team_state_entry(game_state, team_id)
        team_state["actions"] = int(team_state.get("actions") or 0) + 1
        team_state["last_action_at"] = now
        game_state["version"] = int(game_state.get("version") or 0) + 1

        self._repository.update_game_settings_without_commit(db, game_id, settings)
        self._repository.commit_changes(db)

        inventory_after = self._repository.build_inventory_map(db, team_id, resource_name_by_id)
        nearby_points = self.get_nearby_points_for_team(db, game_id, team_id)

        return {
            "trade_id": trade_id,
            "message_key": "market_crash.trade.executed",
            "cash": int(next_cash),
            "score": int(next_cash),
            "inventory": inventory_after,
            "nearby_points": nearby_points,
            "nearby_point_ids": [str(point.get("id") or "") for point in nearby_points],
            "trade": {
                "point_id": point_id,
                "point_title": str(point.get("title") or ""),
                "resource_id": resource_id,
                "resource_name": resource_name,
                "side": normalized_side,
                "quantity": normalized_quantity,
                "unit_price": unit_price,
                "total_amount": total_amount,
            },
            "state_version": int(game_state.get("version") or 0),
            "starting_cash": int(starting_cash),
        }
