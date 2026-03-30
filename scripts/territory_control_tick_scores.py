from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.version_info < (3, 11):
    raise RuntimeError("territory_control_tick_scores.py requires Python 3.11+. Use the workspace venv: ../.venv/bin/python")

from app.database import SessionLocal
from app.repositories.territory_control_repository import TerritoryControlRepository
from app.services.ws_client import WsEventPublisher


repository = TerritoryControlRepository()
publisher = WsEventPublisher()


def _is_game_active(game: dict[str, Any], now: datetime) -> bool:
    start_at = game.get("start_at")
    end_at = game.get("end_at")
    if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
        return False

    start = start_at.replace(tzinfo=UTC) if start_at.tzinfo is None else start_at.astimezone(UTC)
    end = end_at.replace(tzinfo=UTC) if end_at.tzinfo is None else end_at.astimezone(UTC)
    return start <= now <= end


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
        .where(game_table.c["game_type"] == "territory_control")
    ).mappings().all()
    return [dict(row) for row in rows if _is_game_active(dict(row), now)]


def run_cycle(tick_seconds: int) -> tuple[int, int, int]:
    now = datetime.now(UTC)
    changed_games = 0
    awarded_zones = 0
    awarded_points = 0

    with SessionLocal() as db:
        team_table = repository.get_team_table(db)
        team_id_col = repository._get_team_id_column(team_table)
        team_game_id_col = repository._get_team_game_id_column(team_table)
        active_games = _fetch_active_games(db, now)

        for game in active_games:
            game_id = str(game.get("id") or "").strip()
            if not game_id:
                continue

            settings = repository.get_game_settings(db, game_id)
            workers_root = settings.get("_backend_workers")
            if not isinstance(workers_root, dict):
                workers_root = {}
                settings["_backend_workers"] = workers_root
            territory_state = workers_root.get("territory_control")
            if not isinstance(territory_state, dict):
                territory_state = {}
                workers_root["territory_control"] = territory_state
            zone_tick_map = territory_state.get("zone_last_tick")
            if not isinstance(zone_tick_map, dict):
                zone_tick_map = {}
                territory_state["zone_last_tick"] = zone_tick_map

            zones = repository.fetch_zones_by_game_id(db, game_id)
            teams = repository.fetch_teams_by_game_id(db, game_id)
            score_by_team: dict[str, int] = {
                str(team.get("id") or ""): int(team.get("geo_score") or 0)
                for team in teams
                if str(team.get("id") or "")
            }
            awards_by_team: dict[str, int] = {}
            touched_zones = 0

            for zone in zones:
                zone_id = str(zone.get("id") or "").strip()
                owner_team_id = str(zone.get("owner_team_id") or "").strip()
                is_active = bool(zone.get("is_active", True))

                if not zone_id or not owner_team_id or not is_active:
                    if zone_id:
                        zone_tick_map.pop(zone_id, None)
                    continue

                capture_points = max(1, int(zone.get("capture_points") or zone.get("points") or 1))
                last_tick = _parse_iso(zone_tick_map.get(zone_id)) or _parse_iso(zone.get("captured_at")) or now
                elapsed_seconds = (now - last_tick).total_seconds()
                tick_count = int(max(0, elapsed_seconds) // float(tick_seconds))
                if tick_count < 1:
                    continue

                gained = capture_points * tick_count
                awards_by_team[owner_team_id] = int(awards_by_team.get(owner_team_id) or 0) + gained
                zone_tick_map[zone_id] = (last_tick + timedelta(seconds=tick_count * tick_seconds)).isoformat()

                touched_zones += 1
                awarded_zones += 1
                awarded_points += gained

            if not awards_by_team and touched_zones == 0:
                continue

            for team_id, gained in awards_by_team.items():
                previous = score_by_team.get(team_id, 0)
                next_score = previous + gained
                score_by_team[team_id] = next_score
                db.execute(
                    update(team_table)
                    .where(team_table.c[team_game_id_col] == game_id)
                    .where(team_table.c[team_id_col] == team_id)
                    .values(geo_score=next_score)
                )

            repository.update_game_settings_without_commit(db, game_id, settings)
            changed_games += 1

            team_payload = [
                {
                    "team_id": team_id,
                    "score": score,
                    "awarded_points": int(awards_by_team.get(team_id) or 0),
                }
                for team_id, score in score_by_team.items()
                if int(awards_by_team.get(team_id) or 0) > 0
            ]
            payload = {
                "game_id": game_id,
                "updated_at": now.isoformat(),
                "awarded_points": int(sum(awards_by_team.values())),
                "awarded_zones": touched_zones,
                "teams": team_payload,
            }
            publisher.publish("game.territory_control.scores.updated", payload, channels=[f"channel:{game_id}"])
            publisher.publish("admin.territory_control.scores.updated", payload, channels=[f"channel:{game_id}:admin"])
            for team in teams:
                team_id = str(team.get("id") or "").strip()
                if not team_id:
                    continue
                publisher.publish(
                    "team.territory_control.scores.updated",
                    {
                        **payload,
                        "team_id": team_id,
                        "self_awarded_points": int(awards_by_team.get(team_id) or 0),
                    },
                    channels=[f"channel:{game_id}:{team_id}"],
                )

        repository.commit_changes(db)

    return changed_games, awarded_zones, awarded_points


def main() -> None:
    parser = argparse.ArgumentParser(description="Award recurring Territory Control hold-tick points.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--sleep", type=int, default=5, help="Seconds between cycles")
    parser.add_argument("--tick-seconds", type=int, default=60, help="Points tick interval in seconds")
    args = parser.parse_args()

    sleep_seconds = max(1, int(args.sleep))
    tick_seconds = max(1, int(args.tick_seconds))

    while True:
        now = datetime.now(UTC)
        changed_games, zones, points = run_cycle(tick_seconds)
        print(f"[{now.isoformat()}] territory_control_tick_scores games={changed_games} zones={zones} points={points}")
        if args.once:
            break
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()