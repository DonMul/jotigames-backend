from __future__ import annotations

import argparse
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Dict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.version_info < (3, 11):
    raise RuntimeError("market_crash_fluctuate_prices.py requires Python 3.11+. Use the workspace venv: ../.venv/bin/python")

from app.database import SessionLocal
from app.repositories.market_crash_repository import MarketCrashRepository
from app.services.market_crash_service import MarketCrashService
from app.services.ws_client import WsEventPublisher


repository = MarketCrashRepository()
service = MarketCrashService()
publisher = WsEventPublisher()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _due(next_fluctuation_at: Any, now: datetime) -> bool:
    if not isinstance(next_fluctuation_at, datetime):
        return True
    if next_fluctuation_at.tzinfo is not None:
        return next_fluctuation_at.astimezone(UTC).replace(tzinfo=None) <= now
    return next_fluctuation_at <= now


def _adjust_price(base_price: int, fluctuation_percent: float) -> int:
    if base_price <= 0:
        return 1
    capped = max(0.0, min(10.0, float(fluctuation_percent)))
    factor = 1.0 + random.uniform(-(capped / 100.0), (capped / 100.0))
    return max(1, int(round(base_price * factor)))


def run_cycle() -> tuple[int, int, int]:
    now = datetime.now(UTC).replace(tzinfo=None)
    updated_rows = 0
    changed_prices = 0
    changed_games: Dict[str, Dict[str, Dict[str, Any]]] = {}

    with SessionLocal() as db:
        rows = repository.fetch_all_point_resources_with_context(db)
        for row in rows:
            point_resource_id = str(row.get("id") or "")
            game_id = str(row.get("point_game_id") or "")
            point_id = str(row.get("point_id") or row.get("point_table_id") or "")
            resource_id = str(row.get("resource_id") or "")
            resource_name = str(row.get("resource_name") or "")
            if not point_resource_id or not game_id or not point_id or not resource_id:
                continue

            next_at = row.get("next_fluctuation_at")
            if not _due(next_at, now):
                continue

            tick_seconds = max(1, _safe_int(row.get("tick_seconds"), 5))
            fluctuation_percent = max(0.1, min(10.0, _safe_float(row.get("fluctuation_percent"), 10.0)))

            buy_price = row.get("buy_price")
            sell_price = row.get("sell_price")
            updates: Dict[str, Any] = {
                "next_fluctuation_at": now + timedelta(seconds=tick_seconds),
            }
            has_price_change = False

            if buy_price is not None:
                buy_before = _safe_int(buy_price, 1)
                buy_after = _adjust_price(buy_before, fluctuation_percent)
                updates["buy_price"] = buy_after
                if buy_after != buy_before:
                    has_price_change = True

            if sell_price is not None:
                sell_before = _safe_int(sell_price, 1)
                sell_after = _adjust_price(sell_before, fluctuation_percent)
                updates["sell_price"] = sell_after
                if sell_after != sell_before:
                    has_price_change = True

            repository.update_point_resource_without_commit(db, point_resource_id, updates)
            updated_rows += 1

            if has_price_change:
                changed_prices += 1
                if game_id not in changed_games:
                    changed_games[game_id] = {}
                if point_id not in changed_games[game_id]:
                    changed_games[game_id][point_id] = {}

                changed_games[game_id][point_id][resource_id] = {
                    "resource_id": resource_id,
                    "resource_name": resource_name,
                    "buy_price": _safe_int(updates.get("buy_price", buy_price), 0),
                    "sell_price": _safe_int(updates.get("sell_price", sell_price), 0),
                    "tick_seconds": tick_seconds,
                    "fluctuation_percent": fluctuation_percent,
                }

        repository.commit_changes(db)

        for game_id, point_changes in changed_games.items():
            payload = {
                "game_id": game_id,
                "updated_at": now.isoformat(),
                "points": point_changes,
            }
            publisher.publish("admin.market_crash.prices.updated", payload, channels=[f"channel:{game_id}:admin"])
            publisher.publish("game.market_crash.prices.updated", payload, channels=[f"channel:{game_id}"])

            teams = repository.fetch_teams_by_game_id(db, game_id)
            for team in teams:
                team_id = str(team.get("id") or "")
                if not team_id:
                    continue

                nearby_points = service.get_nearby_points_for_team(db, game_id, team_id)
                nearby_ids = {str(point.get("id") or "") for point in nearby_points}
                filtered_changes = {
                    point_id: point_payload
                    for point_id, point_payload in point_changes.items()
                    if point_id in nearby_ids
                }
                if not filtered_changes:
                    continue

                publisher.publish(
                    "team.market_crash.prices.updated",
                    {
                        "game_id": game_id,
                        "team_id": team_id,
                        "updated_at": now.isoformat(),
                        "points": filtered_changes,
                    },
                    channels=[f"channel:{game_id}:{team_id}"],
                )

    return updated_rows, changed_prices, len(changed_games)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fluctuate Market Crash point resource prices.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--sleep", type=int, default=1, help="Seconds between cycles")
    args = parser.parse_args()

    sleep_seconds = max(1, int(args.sleep))

    while True:
        now = datetime.now(UTC)
        updated_rows, changed_prices, changed_games = run_cycle()
        print(
            f"[{now.isoformat()}] market_crash_fluctuation rows={updated_rows} changed_prices={changed_prices} changed_games={changed_games}"
        )
        if args.once:
            break
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
