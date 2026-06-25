"""CLI entry point used locally and by GitHub Actions."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from config import MATCH_TIMEZONE
from logging_config import configure_logging
from pipeline import run_for_date


logger = logging.getLogger(__name__)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today in MATCH_TIMEZONE")
    args = parser.parse_args()
    now = datetime.now(ZoneInfo(MATCH_TIMEZONE))
    logger.info(
        "daily_run.requested",
        extra={"requested_date": args.date, "local_time": now.isoformat()},
    )
    day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else now.date()
    try:
        results = run_for_date(day)
    except Exception:
        logger.exception("daily_run.failed", extra={"match_date": day.isoformat()})
        raise
    logger.info(
        "daily_run.complete",
        extra={"match_date": day.isoformat(), "analyzed_fixtures": len(results)},
    )


if __name__ == "__main__":
    main()
