"""Daily subscription renewal & cancellation cron job.

Runs once per day (preferably at 00:05 UTC).
Two responsibilities:
  1. Renew active subscriptions whose current_period_end <= now.
  2. Process pending-cancel subscriptions whose current_period_end <= now.

Usage:
    .venv/bin/python backend/scripts/renew_subscriptions.py          # single pass
    .venv/bin/python backend/scripts/renew_subscriptions.py --loop    # continuous (every 3600 s)
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

if sys.version_info < (3, 11):
    raise RuntimeError("renew_subscriptions.py requires Python 3.11+. Use the workspace venv: ../.venv/bin/python")

from app.config import get_settings
from app.database import SessionLocal
from app.services.subscription_service import SubscriptionService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [renew_subscriptions] %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

service = SubscriptionService()


def _now_utc() -> datetime:
    return datetime.now(UTC)


def run_cycle() -> dict:
    """Execute a single renewal cycle.

    Returns dict with counts: renewed, renewal_errors, cancelled,
    cancellation_errors.
    """
    settings = get_settings()
    if not settings.enable_monetisation:
        log.debug("Monetisation disabled – skipping cycle")
        return {"renewed": 0, "renewal_errors": 0, "cancelled": 0, "cancellation_errors": 0}

    now = _now_utc()
    stats = {"renewed": 0, "renewal_errors": 0, "cancelled": 0, "cancellation_errors": 0}

    # ── Renewals ──────────────────────────────────────────────────────────
    with SessionLocal() as db:
        from app.repositories.subscription_repository import SubscriptionRepository
        repo = SubscriptionRepository()
        due_subs = repo.list_subscriptions_due_for_renewal(db, now.replace(tzinfo=None))

        for sub in due_subs:
            try:
                service.renew_period(db, sub.user_id)
                stats["renewed"] += 1
                log.info("Renewed subscription for user %s", sub.user_id)
            except Exception:
                stats["renewal_errors"] += 1
                log.exception("Failed to renew subscription for user %s", sub.user_id)

    # ── Pending cancellations ─────────────────────────────────────────────
    with SessionLocal() as db:
        repo = SubscriptionRepository()
        pending = repo.list_subscriptions_pending_cancel(db, now.replace(tzinfo=None))

        for sub in pending:
            try:
                service.process_pending_cancellations(db)
                stats["cancelled"] += 1
                log.info("Processed cancellation for user %s", sub.user_id)
            except Exception:
                stats["cancellation_errors"] += 1
                log.exception("Failed to cancel subscription for user %s", sub.user_id)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Daily subscription renewal & cancellation cron")
    parser.add_argument("--loop", action="store_true", help="Run continuously with a sleep")
    parser.add_argument("--interval", type=int, default=3600, help="Seconds between cycles in loop mode (default 1h)")
    args = parser.parse_args()

    if args.loop:
        log.info("Starting continuous loop (interval=%d s)", args.interval)
        while True:
            try:
                stats = run_cycle()
                log.info("Cycle complete: %s", stats)
            except Exception:
                log.exception("Error in renewal cycle")
            time.sleep(args.interval)
    else:
        stats = run_cycle()
        log.info("Single pass: %s", stats)


if __name__ == "__main__":
    main()
