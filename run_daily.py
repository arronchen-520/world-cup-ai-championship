"""CLI entry point used locally and by GitHub Actions."""

from __future__ import annotations

import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from config import MATCH_TIMEZONE
from pipeline import run_for_date


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="YYYY-MM-DD; defaults to today in MATCH_TIMEZONE")
    parser.add_argument("--midnight-guard", action="store_true", help="Exit unless local hour is midnight")
    args = parser.parse_args()
    now = datetime.now(ZoneInfo(MATCH_TIMEZONE))
    if args.midnight_guard and now.hour != 0:
        print(f"Skipping: it is {now:%H:%M} in {MATCH_TIMEZONE}, not midnight.")
        return
    day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else now.date()
    results = run_for_date(day)
    print(f"Analyzed {len(results)} fixture(s) for {day}.")


if __name__ == "__main__":
    main()
