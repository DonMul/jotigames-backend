from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.version_info < (3, 11):
    raise RuntimeError("courier_rush_spawn_points.py requires Python 3.11+. Use the workspace venv: ../.venv/bin/python")

from app.database import SessionLocal
from app.repositories.courier_rush_repository import CourierRushRepository
from app.services.ws_client import WsEventPublisher


repository = CourierRushRepository()
publisher = WsEventPublisher()

_AUTO_PREFIX_PICKUP = "Auto Pickup"
_AUTO_PREFIX_DROPOFF = "Auto Dropoff"


def _is_game_active(game: dict[str, Any], now: datetime) -> bool:
    start_at = game.get("start_at")
    end_at = game.get("end_at")
    if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
        return False

    start = start_at.replace(tzinfo=UTC) if start_at.tzinfo is None else start_at.astimezone(UTC)
    end = end_at.replace(tzinfo=UTC) if end_at.tzinfo is None else end_at.astimezone(UTC)
    return start <= now <= end


def _parse_polygon(raw: Any) -> list[tuple[float, float]] | None:
    text = str(raw or "").strip()
    if not text:
        return None

    try:
        decoded = json.loads(text)
    except Exception:
        return None

    if not isinstance(decoded, dict):
        return None

    geometry = decoded
    if decoded.get("type") == "Feature" and isinstance(decoded.get("geometry"), dict):
        geometry = decoded["geometry"]

    if geometry.get("type") != "Polygon":
        return None

    rings = geometry.get("coordinates")
    if not isinstance(rings, list) or not rings or not isinstance(rings[0], list):
        return None

    polygon: list[tuple[float, float]] = []
    for pair in rings[0]:
        if not isinstance(pair, list) or len(pair) < 2:
            continue
        try:
            lon = float(pair[0])
            lat = float(pair[1])
        except (TypeError, ValueError):
            continue
        polygon.append((lon, lat))

    return polygon if len(polygon) >= 3 else None


def _point_in_polygon(lon: float, lat: float, polygon: list[tuple[float, float]]) -> bool:
    inside = False
    count = len(polygon)
    for index in range(count):
        x1, y1 = polygon[index]
        x2, y2 = polygon[index - 1]
        intersects = ((y1 > lat) != (y2 > lat)) and (lon < ((x2 - x1) * (lat - y1)) / ((y2 - y1) or 1e-12) + x1)
        if intersects:
            inside = not inside
    return inside


def _random_point_in_polygon(polygon: list[tuple[float, float]]) -> tuple[float, float] | None:
    min_lon = min(point[0] for point in polygon)
    max_lon = max(point[0] for point in polygon)
    min_lat = min(point[1] for point in polygon)
    max_lat = max(point[1] for point in polygon)

    for _ in range(80):
        lon = random.uniform(min_lon, max_lon)
        lat = random.uniform(min_lat, max_lat)
        if _point_in_polygon(lon, lat, polygon):
            return lat, lon

    return None


def _is_auto_pickup(pickup: dict[str, Any]) -> bool:
    title = str(pickup.get("title") or "").strip()
    return title.startswith(_AUTO_PREFIX_PICKUP)


def _is_auto_dropoff(dropoff: dict[str, Any]) -> bool:
    title = str(dropoff.get("title") or "").strip()
    return title.startswith(_AUTO_PREFIX_DROPOFF)


def _fetch_active_game_rows(db: Any, now: datetime) -> list[dict[str, Any]]:
    game_table = repository.get_game_table(db)
    rows = db.execute(
        select(game_table)
        .where(game_table.c["game_type"] == "courier_rush")
    ).mappings().all()
    return [dict(row) for row in rows if _is_game_active(dict(row), now)]


def _spawn_pickup(db: Any, game_id: str, latitude: float, longitude: float) -> None:
    repository.create_pickup_without_commit(
        db,
        {
            "id": str(uuid4()),
            "game_id": game_id,
            "title": f"{_AUTO_PREFIX_PICKUP} {random.randint(100, 999)}",
            "latitude": latitude,
            "longitude": longitude,
            "radius_meters": 25,
            "points": 5,
            "marker_color": "#2563eb",
            "is_active": True,
        },
    )


def _spawn_dropoff(db: Any, game_id: str, latitude: float, longitude: float) -> None:
    repository.create_dropoff_without_commit(
        db,
        {
            "id": str(uuid4()),
            "game_id": game_id,
            "title": f"{_AUTO_PREFIX_DROPOFF} {random.randint(100, 999)}",
            "latitude": latitude,
            "longitude": longitude,
            "radius_meters": 25,
            "marker_color": "#16a34a",
            "is_active": True,
        },
    )


def run_cycle() -> tuple[int, int, int]:
    now = datetime.now(UTC)
    changed_games = 0
    spawned_pickups = 0
    spawned_dropoffs = 0

    with SessionLocal() as db:
        active_games = _fetch_active_game_rows(db, now)
        for game in active_games:
            game_id = str(game.get("id") or "").strip()
            if not game_id:
                continue

            config = repository.get_configuration(db, game_id)
            pickup_mode = str(config.get("pickup_mode") or "predefined")
            dropoff_mode = str(config.get("dropoff_mode") or "fixed")
            max_active_pickups = max(1, int(config.get("max_active_pickups") or 3))
            polygon = _parse_polygon(config.get("pickup_spawn_area_geojson"))
            if polygon is None:
                continue

            game_changed = False

            if pickup_mode == "random":
                pickups = repository.fetch_pickups_by_game_id(db, game_id)
                active_pickups = [pickup for pickup in pickups if bool(pickup.get("is_active", True))]
                auto_active_pickups = [pickup for pickup in active_pickups if _is_auto_pickup(pickup)]

                if len(active_pickups) > max_active_pickups and auto_active_pickups:
                    overflow = len(active_pickups) - max_active_pickups
                    for pickup in auto_active_pickups[:overflow]:
                        pickup_id = str(pickup.get("id") or "")
                        if not pickup_id:
                            continue
                        repository.update_pickup_without_commit(db, game_id, pickup_id, {"is_active": False})
                        game_changed = True

                current_active_pickups = max_active_pickups
                refreshed_pickups = repository.fetch_pickups_by_game_id(db, game_id)
                current_active_pickups = len([pickup for pickup in refreshed_pickups if bool(pickup.get("is_active", True))])
                while current_active_pickups < max_active_pickups:
                    point = _random_point_in_polygon(polygon)
                    if point is None:
                        break
                    latitude, longitude = point
                    _spawn_pickup(db, game_id, latitude, longitude)
                    spawned_pickups += 1
                    current_active_pickups += 1
                    game_changed = True

            if dropoff_mode == "random":
                target_dropoffs = max(1, min(5, max_active_pickups))
                dropoffs = repository.fetch_dropoffs_by_game_id(db, game_id)
                active_dropoffs = [dropoff for dropoff in dropoffs if bool(dropoff.get("is_active", True))]
                auto_active_dropoffs = [dropoff for dropoff in active_dropoffs if _is_auto_dropoff(dropoff)]

                if len(active_dropoffs) > target_dropoffs and auto_active_dropoffs:
                    overflow = len(active_dropoffs) - target_dropoffs
                    for dropoff in auto_active_dropoffs[:overflow]:
                        dropoff_id = str(dropoff.get("id") or "")
                        if not dropoff_id:
                            continue
                        repository.update_dropoff_without_commit(db, game_id, dropoff_id, {"is_active": False})
                        game_changed = True

                refreshed_dropoffs = repository.fetch_dropoffs_by_game_id(db, game_id)
                current_active_dropoffs = len([dropoff for dropoff in refreshed_dropoffs if bool(dropoff.get("is_active", True))])
                while current_active_dropoffs < target_dropoffs:
                    point = _random_point_in_polygon(polygon)
                    if point is None:
                        break
                    latitude, longitude = point
                    _spawn_dropoff(db, game_id, latitude, longitude)
                    spawned_dropoffs += 1
                    current_active_dropoffs += 1
                    game_changed = True

            if not game_changed:
                continue

            changed_games += 1
            payload = {
                "game_id": game_id,
                "updated_at": now.isoformat(),
                "spawned_pickups": spawned_pickups,
                "spawned_dropoffs": spawned_dropoffs,
            }
            publisher.publish("game.courier_rush.state.updated", payload, channels=[f"channel:{game_id}"])
            publisher.publish("admin.courier_rush.state.updated", payload, channels=[f"channel:{game_id}:admin"])

            teams = repository.fetch_teams_by_game_id(db, game_id)
            for team in teams:
                team_id = str(team.get("id") or "").strip()
                if not team_id:
                    continue
                publisher.publish(
                    "team.courier_rush.state.updated",
                    {
                        **payload,
                        "team_id": team_id,
                    },
                    channels=[f"channel:{game_id}:{team_id}"],
                )

        repository.commit_changes(db)

    return changed_games, spawned_pickups, spawned_dropoffs


def main() -> None:
    parser = argparse.ArgumentParser(description="Spawn and maintain Courier Rush random pickup/dropoff points.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--sleep", type=int, default=5, help="Seconds between cycles")
    args = parser.parse_args()

    sleep_seconds = max(1, int(args.sleep))

    while True:
        now = datetime.now(UTC)
        changed_games, spawned_pickups, spawned_dropoffs = run_cycle()
        print(
            f"[{now.isoformat()}] courier_rush_spawn_points games={changed_games} spawned_pickups={spawned_pickups} spawned_dropoffs={spawned_dropoffs}"
        )
        if args.once:
            break
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()