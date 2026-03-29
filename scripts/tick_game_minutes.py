"""Tick game-minutes cron job.

Runs every minute (or at whatever interval the host crontab is configured).
For each currently-active game (start_at <= now <= end_at) it counts the number
of teams and deducts ``1 × team_count`` minutes from the game owner's balance.

Usage:
    .venv/bin/python backend/scripts/tick_game_minutes.py          # single pass
    .venv/bin/python backend/scripts/tick_game_minutes.py --loop    # continuous (every 60 s)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import MetaData, Table, func, select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.version_info < (3, 11):
    raise RuntimeError("tick_game_minutes.py requires Python 3.11+. Use the workspace venv: ../.venv/bin/python")

from app.config import get_settings
from app.database import SessionLocal
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.subscription_service import SubscriptionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [tick_game_minutes] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

service = SubscriptionService()
repo = SubscriptionRepository()


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _is_active(game: dict, now: datetime) -> bool:
    start_at = game.get("start_at")
    end_at = game.get("end_at")
    if not isinstance(start_at, datetime) or not isinstance(end_at, datetime):
        return False
    start = start_at.replace(tzinfo=UTC) if start_at.tzinfo is None else start_at.astimezone(UTC)
    end = end_at.replace(tzinfo=UTC) if end_at.tzinfo is None else end_at.astimezone(UTC)
    return start <= now <= end


def run_cycle() -> tuple[int, int]:
    """Execute a single tick cycle.

    Returns (games_processed, total_minutes_deducted).
    """
    settings = get_settings()
    if not settings.enable_monetisation:
        log.debug("Monetisation disabled – skipping cycle")
        return 0, 0

    now = _now_utc()
    games_processed = 0
    total_minutes = 0

    with SessionLocal() as db:
        metadata = MetaData()
        metadata.reflect(bind=db.get_bind())

        game_table = metadata.tables.get("game")
        team_table = metadata.tables.get("team")

        if game_table is None or team_table is None:
            log.warning("game or team table not found – skipping")
            return 0, 0

        # Fetch all games with a start_at/end_at that bracket now
        rows = db.execute(select(game_table)).mappings().all()

        for row in rows:
            game = dict(row)
            if not _is_active(game, now):
                continue

            game_id = str(game.get("id") or "").strip()
            owner_id = str(game.get("owner_id") or "").strip()
            if not game_id or not owner_id:
                continue

            # Count teams in this game
            team_count_result = db.execute(
                select(func.count()).select_from(team_table).where(team_table.c["game_id"] == game_id)
            ).scalar()
            team_count = int(team_count_result or 0)
            if team_count == 0:
                continue

            # Each tick interval consumes team_count minutes from the owner
            minutes_to_deduct = team_count
            ok = service.consume_minutes(db, owner_id, minutes_to_deduct)
            if ok:
                games_processed += 1
                total_minutes += minutes_to_deduct
                log.info(
                    "Game %s (owner=%s): deducted %d minutes (%d teams)",
                    game_id,
                    owner_id,
                    minutes_to_deduct,
                    team_count,
                )
            else:
                log.warning(
                    "Game %s (owner=%s): insufficient minutes for %d teams – "
                    "balance exhausted",
                    game_id,
                    owner_id,
                    team_count,
                )

    return games_processed, total_minutes


def main() -> None:
    parser = argparse.ArgumentParser(description="Tick game-minutes billing cron")
    parser.add_argument("--loop", action="store_true", help="Run continuously with a 60 s sleep")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between cycles in loop mode")
    args = parser.parse_args()

    if args.loop:
        log.info("Starting continuous loop (interval=%d s)", args.interval)
        while True:
            try:
                games, minutes = run_cycle()
                log.info("Cycle complete: %d games, %d minutes deducted", games, minutes)
            except Exception:
                log.exception("Error in tick cycle")
            time.sleep(args.interval)
    else:
        games, minutes = run_cycle()
        log.info("Single pass: %d games, %d minutes deducted", games, minutes)


if __name__ == "__main__":
    main()
