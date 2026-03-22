from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.version_info < (3, 11):
    raise RuntimeError("birds_of_prey_auto_drop_eggs.py requires Python 3.11+. Use the workspace venv: ../.venv/bin/python")

from app.database import SessionLocal
from app.repositories.birds_of_prey_repository import BirdsOfPreyRepository
from app.services.birds_of_prey_service import BirdsOfPreyService
from app.services.ws_client import WsEventPublisher


service = BirdsOfPreyService()
repository = BirdsOfPreyRepository()
publisher = WsEventPublisher()


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _is_game_active(game: dict, now: datetime) -> bool:
    start_at = game.get("start_at")
    end_at = game.get("end_at")
    if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
        return False

    start = start_at.replace(tzinfo=UTC) if start_at.tzinfo is None else start_at.astimezone(UTC)
    end = end_at.replace(tzinfo=UTC) if end_at.tzinfo is None else end_at.astimezone(UTC)
    return start <= now <= end


def run_cycle() -> tuple[int, int]:
    now = _now_utc()
    processed_games = 0
    dropped_eggs = 0

    with SessionLocal() as db:
        game_table = repository.get_game_table(db)
        rows = db.execute(
            select(game_table)
            .where(game_table.c["game_type"] == "birds_of_prey")
        ).mappings().all()

        for row in rows:
            game = dict(row)
            game_id = str(game.get("id") or "").strip()
            if not game_id or not _is_game_active(game, now):
                continue

            processed_games += 1
            config = repository.get_configuration(db, game_id)
            auto_drop_seconds = max(30, int(config.get("auto_drop_seconds") or 300))
            teams = repository.fetch_teams_by_game_id(db, game_id)
            team_names = {str(team.get("id") or ""): str(team.get("name") or "") for team in teams}

            for team in teams:
                team_id = str(team.get("id") or "").strip()
                if not team_id:
                    continue

                location = repository.get_team_location(db, game_id, team_id)
                try:
                    lat = float(location.get("lat")) if location.get("lat") is not None else None
                    lon = float(location.get("lon")) if location.get("lon") is not None else None
                except (TypeError, ValueError):
                    lat = None
                    lon = None
                if lat is None or lon is None:
                    continue

                last_drop_at = service.get_last_drop_at_for_team(db, game_id=game_id, team_id=team_id)
                if last_drop_at is not None:
                    elapsed = (now - last_drop_at).total_seconds()
                    if elapsed < float(auto_drop_seconds):
                        continue

                egg_id = f"auto:{team_id}:{int(now.timestamp())}"
                try:
                    service.drop_egg(db, game_id=game_id, team_id=team_id, egg_id=egg_id, automatic=True)
                except ValueError:
                    continue

                dropped_eggs += 1
                eggs = service.get_active_eggs(db, game_id=game_id)
                egg = eggs.get(egg_id)
                if not isinstance(egg, dict):
                    continue

                egg_payload = {
                    "game_id": game_id,
                    "id": egg_id,
                    "owner_team_id": team_id,
                    "owner_team_name": team_names.get(team_id, team_id),
                    "lat": egg.get("lat"),
                    "lon": egg.get("lon"),
                    "dropped_at": egg.get("dropped_at"),
                    "automatic": True,
                }
                publisher.publish(
                    "admin.birds_of_prey.egg.added",
                    egg_payload,
                    channels=[f"channel:{game_id}:admin"],
                )
                publisher.publish(
                    "team.birds_of_prey.egg.added",
                    egg_payload,
                    channels=[f"channel:{game_id}:{team_id}"],
                )
                for viewer_team in teams:
                    viewer_team_id = str(viewer_team.get("id") or "").strip()
                    if not viewer_team_id:
                        continue
                    visible_enemy_eggs = service.get_visible_enemy_eggs_for_team(db, game_id=game_id, team_id=viewer_team_id)
                    publisher.publish(
                        "team.birds_of_prey.enemy_eggs.visible",
                        {
                            "game_id": game_id,
                            "team_id": viewer_team_id,
                            "eggs": visible_enemy_eggs,
                        },
                        channels=[f"channel:{game_id}:{viewer_team_id}"],
                    )
                publisher.publish(
                    "team.general.message",
                    {
                        "teamId": team_id,
                        "id": f"autodrop:{egg_id}",
                        "message": "",
                        "message_key": "teamDashboard.birdsOfPrey.autoDroppedMessage",
                        "messageKey": "teamDashboard.birdsOfPrey.autoDroppedMessage",
                        "message_params": {},
                        "messageParams": {},
                        "title": "",
                        "title_key": "teamDashboard.popupTitle",
                        "titleKey": "teamDashboard.popupTitle",
                        "level": "info",
                        "from": "system",
                        "gameId": game_id,
                        "createdAt": now.isoformat(),
                    },
                    channels=[f"channel:{game_id}:{team_id}"],
                )

    return processed_games, dropped_eggs


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-drop Birds of Prey eggs for active games.")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    parser.add_argument("--sleep", type=int, default=5, help="Seconds between cycles")
    args = parser.parse_args()

    sleep_seconds = max(1, int(args.sleep))

    while True:
        now = _now_utc()
        games, eggs = run_cycle()
        print(f"[{now.isoformat()}] birds_of_prey_autodrop games={games} dropped={eggs}")

        if args.once:
            break
        time.sleep(sleep_seconds)


if __name__ == "__main__":
    main()
