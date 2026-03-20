from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from alembic import command
from alembic.config import Config

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config import get_settings, normalize_database_url  # noqa: E402

def run_setup(target_revision: str = "head") -> None:
    original_cwd = Path.cwd()
    os.chdir(BACKEND_ROOT)
    try:
        settings = get_settings()
        normalized_database_url = normalize_database_url(settings.database_url)

        if not (
            normalized_database_url.startswith("mysql+pymysql://")
            or normalized_database_url.startswith("mysql://")
        ):
            raise RuntimeError(
                "setup_database.py only supports MySQL DATABASE_URL values "
                "(expected mysql:// or mysql+pymysql://)."
            )

        alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
        alembic_config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
        alembic_config.set_main_option("sqlalchemy.url", normalized_database_url)

        command.upgrade(alembic_config, target_revision)
    finally:
        os.chdir(original_cwd)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Set up backend database schema by running Alembic migrations.",
    )
    parser.add_argument(
        "--revision",
        default="head",
        help="Alembic target revision to upgrade to (default: head).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_setup(target_revision=args.revision)


if __name__ == "__main__":
    main()
