"""Expire unused top-up minutes after their 12-month validity window.

Usage:
    .venv/bin/python backend/scripts/expire_topup_minutes.py           # single pass
    .venv/bin/python backend/scripts/expire_topup_minutes.py --loop    # continuous
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.version_info < (3, 11):
    raise RuntimeError("expire_topup_minutes.py requires Python 3.11+. Use the workspace venv: ../.venv/bin/python")

from app.config import get_settings
from app.database import SessionLocal
from app.services.subscription_service import SubscriptionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [expire_topup_minutes] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

service = SubscriptionService()


def run_cycle() -> int:
    settings = get_settings()
    if not settings.enable_monetisation:
        log.debug("Monetisation disabled – skipping cycle")
        return 0

    with SessionLocal() as db:
        expired = service.expire_elapsed_topups(db)
        return int(expired)


def main() -> None:
    parser = argparse.ArgumentParser(description="Expire old top-up minutes")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between cycles in loop mode")
    args = parser.parse_args()

    if args.loop:
        log.info("Starting continuous loop (interval=%d s)", args.interval)
        while True:
            try:
                expired = run_cycle()
                log.info("Cycle complete: expired %d top-up purchases", expired)
            except Exception:
                log.exception("Error in top-up expiry cycle")
            time.sleep(args.interval)
    else:
        expired = run_cycle()
        log.info("Single pass: expired %d top-up purchases", expired)


if __name__ == "__main__":
    main()
