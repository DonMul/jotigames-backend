from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import insert, select, update

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.version_info < (3, 11):
    raise RuntimeError("pandemic_response_spawn_hotspots.py requires Python 3.11+. Use the workspace venv: ../.venv/bin/python")

from app.database import SessionLocal
from app.repositories.pandemic_response_repository import PandemicResponseRepository
from app.services.ws_client import WsEventPublisher


repository = PandemicResponseRepository()
publisher = WsEventPublisher()

_HOTSPOT_COLORS = ["#dc2626", "#f59e0b", "#16a34a"]
_PICKUP_RESOURCE_TYPES = [
    ("first_aid", "First Aid Cache", "#2563eb"),
    ("portable_lab", "Portable Lab Point", "#7c3aed"),
    ("field_kit", "Field Kit Depot", "#0f766e"),
]


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


def _random_point_around(center_lat: float, center_lon: float, radius_meters: int = 450) -> tuple[float, float]:
    angle = random.random() * 360.0
    distance = (random.random() ** 0.5) * max(10, radius_meters)

    lat_offset = (distance / 111_320.0) * __import__("math").cos(__import__("math").radians(angle))
    lon_divisor = max(0.0001, 111_320.0 * __import__("math").cos(__import__("math").radians(center_lat)))
    lon_offset = (distance / lon_divisor) * __import__("math").sin(__import__("math").radians(angle))
    return center_lat + lat_offset, center_lon + lon_offset


def _table_values(table: Any, values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if key in table.c}


def _parse_iso(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=UTC) if raw.tzinfo is None else raw.astimezone(UTC)
    text = str(raw or "").strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _fetch_active_games(db: Any, now: datetime) -> list[dict[str, Any]]:
    game_table = repository.get_game_table(db)
    rows = db.execute(
        select(game_table)
        .where(game_table.c["game_type"] == "pandemic_response")
    ).mappings().all()
    return [dict(row) for row in rows if _is_game_active(dict(row), now)]


def run_cycle() -> tuple[int, int, int, int]:
    now = datetime.now(UTC)
    changed_games = 0
    spawned_hotspots = 0
    spawned_pickups = 0
    escalations = 0

    with SessionLocal() as db:
        hotspot_table = repository.get_hotspot_table(db)
        pickup_table = repository.get_pickup_table(db)
        team_table = repository.get_team_table(db)
        team_id_col = repository._get_team_id_column(team_table)
        team_game_id_col = repository._get_team_game_id_column(team_table)

        games = _fetch_active_games(db, now)
        for game in games:
            game_id = str(game.get("id") or "").strip()
            if not game_id:
                continue

            config = repository.get_configuration(db, game_id)
            polygon = _parse_polygon(config.get("spawn_area_geojson"))
            center_lat = float(config.get("center_lat") or 51.05)
            center_lon = float(config.get("center_lon") or 3.72)
            target_hotspots = max(1, int(config.get("target_active_hotspots") or 15))
            target_pickups = max(1, int(config.get("pickup_point_count") or 4))
            penalty_percent = max(1, min(90, int(config.get("penalty_percent") or 10)))
            interval_seconds = max(30, int(config.get("severity_upgrade_seconds") or 180))

            settings = repository.get_game_settings(db, game_id)
            workers_root = settings.get("_backend_workers")
            if not isinstance(workers_root, dict):
                workers_root = {}
                settings["_backend_workers"] = workers_root
            game_worker_state = workers_root.get("pandemic_response")
            if not isinstance(game_worker_state, dict):
                game_worker_state = {}
                workers_root["pandemic_response"] = game_worker_state
            escalation_map = game_worker_state.get("hotspot_escalation_at")
            if not isinstance(escalation_map, dict):
                escalation_map = {}
                game_worker_state["hotspot_escalation_at"] = escalation_map

            game_changed = False
            game_spawned_hotspots = 0
            game_spawned_pickups = 0
            game_escalations = 0
            penalties_applied = 0

            hotspots = repository.fetch_hotspots_by_game_id(db, game_id)
            active_hotspots = [hotspot for hotspot in hotspots if bool(hotspot.get("is_active", True))]

            for hotspot in active_hotspots:
                hotspot_id = str(hotspot.get("id") or "").strip()
                if not hotspot_id:
                    continue

                base = _parse_iso(escalation_map.get(hotspot_id)) or _parse_iso(hotspot.get("updated_at")) or now
                elapsed = (now - base).total_seconds()
                if elapsed < float(interval_seconds):
                    continue

                severity = int(hotspot.get("severity_level") or 1)
                points = max(1, int(hotspot.get("points") or 1))
                if severity < 3:
                    next_values = _table_values(
                        hotspot_table,
                        {
                            "severity_level": severity + 1,
                            "points": points + 2,
                            "updated_at": now.replace(tzinfo=None),
                        },
                    )
                    db.execute(
                        update(hotspot_table)
                        .where(hotspot_table.c["game_id"] == game_id)
                        .where(hotspot_table.c["id"] == hotspot_id)
                        .values(**next_values)
                    )
                    escalation_map[hotspot_id] = now.isoformat()
                    escalations += 1
                    game_escalations += 1
                    game_changed = True
                    continue

                disable_values = _table_values(hotspot_table, {"is_active": False, "updated_at": now.replace(tzinfo=None)})
                db.execute(
                    update(hotspot_table)
                    .where(hotspot_table.c["game_id"] == game_id)
                    .where(hotspot_table.c["id"] == hotspot_id)
                    .values(**disable_values)
                )
                escalation_map.pop(hotspot_id, None)

                teams = repository.fetch_teams_by_game_id(db, game_id)
                for team in teams:
                    team_id = str(team.get("id") or "").strip()
                    score = max(0, int(team.get("geo_score") or 0))
                    if not team_id or score <= 0:
                        continue
                    penalty = max(1, int(score * (penalty_percent / 100.0)))
                    next_score = max(0, score - penalty)
                    db.execute(
                        update(team_table)
                        .where(team_table.c[team_game_id_col] == game_id)
                        .where(team_table.c[team_id_col] == team_id)
                        .values(geo_score=next_score)
                    )

                penalties_applied += 1
                game_changed = True

            pickups = repository.fetch_pickups_by_game_id(db, game_id)
            active_pickups = [pickup for pickup in pickups if bool(pickup.get("is_active", True))]
            while len(active_pickups) < target_pickups:
                resource_type, title_prefix, color = _PICKUP_RESOURCE_TYPES[len(active_pickups) % len(_PICKUP_RESOURCE_TYPES)]
                point = _random_point_in_polygon(polygon) if polygon else _random_point_around(center_lat, center_lon, 320)
                if point is None:
                    break
                lat, lon = point
                values = _table_values(
                    pickup_table,
                    {
                        "id": str(uuid4()),
                        "game_id": game_id,
                        "title": f"{title_prefix} {len(active_pickups) + 1}",
                        "resource_type": resource_type,
                        "latitude": lat,
                        "longitude": lon,
                        "radius_meters": 30,
                        "marker_color": color,
                        "is_active": True,
                        "created_at": now.replace(tzinfo=None),
                        "updated_at": now.replace(tzinfo=None),
                    },
                )
                db.execute(insert(pickup_table).values(**values))
                active_pickups.append(values)
                spawned_pickups += 1
                game_spawned_pickups += 1
                game_changed = True

            refreshed_hotspots = repository.fetch_hotspots_by_game_id(db, game_id)
            active_after = [hotspot for hotspot in refreshed_hotspots if bool(hotspot.get("is_active", True))]
            missing_hotspots = max(0, target_hotspots - len(active_after))
            for index in range(missing_hotspots):
                point = _random_point_in_polygon(polygon) if polygon else _random_point_around(center_lat, center_lon, 450)
                if point is None:
                    break
                lat, lon = point
                color = random.choice(_HOTSPOT_COLORS)
                values = _table_values(
                    hotspot_table,
                    {
                        "id": str(uuid4()),
                        "game_id": game_id,
                        "title": f"Auto Hotspot {now.strftime('%H%M%S')}-{index + 1}",
                        "latitude": lat,
                        "longitude": lon,
                        "radius_meters": random.randint(20, 65),
                        "points": random.randint(3, 12),
                        "severity_level": 1,
                        "marker_color": color,
                        "is_active": True,
                        "created_at": now.replace(tzinfo=None),
                        "updated_at": now.replace(tzinfo=None),
                    },
                )
                hotspot_id = str(values.get("id") or "")
                db.execute(insert(hotspot_table).values(**values))
                if hotspot_id:
                    escalation_map[hotspot_id] = now.isoformat()
                spawned_hotspots += 1
                game_spawned_hotspots += 1
                game_changed = True

            if not game_changed:
                continue

            changed_games += 1
            repository.update_game_settings_without_commit(db, game_id, settings)

            hotspots_payload = {
                "game_id": game_id,
                "updated_at": now.isoformat(),
                "spawned_count": game_spawned_hotspots,
                "escalated_count": game_escalations,
                "penalties_count": penalties_applied,
            }
            pickups_payload = {
                "game_id": game_id,
                "updated_at": now.isoformat(),
                "spawned_count": game_spawned_pickups,
            }

            publisher.publish("game.pandemic_response.hotspots.updated", hotspots_payload, channels=[f"channel:{game_id}"])
            publisher.publish("admin.pandemic_response.hotspots.updated", hotspots_payload, channels=[f"channel:{game_id}:admin"])
            if game_spawned_pickups > 0:
                publisher.publish("game.pandemic_response.pickups.updated", pickups_payload, channels=[f"channel:{game_id}"])
                publisher.publish("admin.pandemic_response.pickups.updated", pickups_payload, channels=[f"channel:{game_id}:admin"])

            teams = repository.fetch_teams_by_game_id(db, game_id)
            for team in teams:
                team_id = str(team.get("id") or "").strip()
                if not team_id:
                    continue
                publisher.publish(
                    "team.pandemic_response.hotspots.updated",
                    {**hotspots_payload, "team_id": team_id},
                    channels=[f"channel:{game_id}:{team_id}"],
                )
                if game_spawned_pickups > 0:
                    publisher.publish(
                        "team.pandemic_response.pickups.updated",
                        {**pickups_payload, "team_id": team_id},
                        channels=[f"channel:{game_id}:{team_id}"],
                    )

        repository.commit_changes(db)

    return changed_games, spawned_hotspots, spawned_pickups, escalations


def main() -> None:
    parser = argparse.ArgumentParser(description="Spawn and escalate Pandemic Response hotspots/pickups.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--sleep", type=int, default=5, help="Seconds between cycles")
    args = parser.parse_args()

    sleep_seconds = max(1, int(args.sleep))

    while True:
        now = datetime.now(UTC)
        changed_games, hotspots, pickups, escalated = run_cycle()
        print(
            f"[{now.isoformat()}] pandemic_response_spawn_hotspots games={changed_games} spawned_hotspots={hotspots} spawned_pickups={pickups} escalations={escalated}"
        )
        if args.once:
            break
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()