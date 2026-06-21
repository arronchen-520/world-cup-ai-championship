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
    parser.add_argument("--midnight-guard", action="store_true", help="Exit unless local hour is midnight")
    args = parser.parse_args()
    now = datetime.now(ZoneInfo(MATCH_TIMEZONE))
    logger.info(
        "daily_run.requested",
        extra={"requested_date": args.date, "midnight_guard": args.midnight_guard, "local_time": now.isoformat()},
    )
    if args.midnight_guard and now.hour != 0:
        logger.info(
            "daily_run.skipped_midnight_guard",
            extra={"local_hour": now.hour, "timezone": MATCH_TIMEZONE},
        )
        return
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
