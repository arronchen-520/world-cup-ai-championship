"""End-to-end daily analysis orchestration."""

from __future__ import annotations

import logging
import time
from datetime import date, timedelta

from config import EVALUATION_MODEL, MASTER_MODEL, MODELS
from database import (
    get_day, get_pending_evaluation_dates, initialize_database,
    save_analysis, save_evaluation, save_match,
)


logger = logging.getLogger(__name__)
from services import (
    analyst_prompt, evaluate_analysts, get_fixtures_football_data, master_prompt,
    openrouter_chat, research_match, run_analysts,
)


def refresh_and_evaluate_previous(day: date) -> int:
    """Refresh yesterday plus unfinished backlog and score newly finished matches."""
    yesterday = (day - timedelta(days=1)).isoformat()
    pending_dates = get_pending_evaluation_dates(day.isoformat())
    dates_to_refresh = list(dict.fromkeys([yesterday, *pending_dates]))
    started = time.perf_counter()
    logger.info(
        "evaluation_backlog.start",
        extra={"run_date": day.isoformat(), "dates": dates_to_refresh, "date_count": len(dates_to_refresh)},
    )
    evaluated = 0
    for match_date in dates_to_refresh:
        fixtures = get_fixtures_football_data(date.fromisoformat(match_date))
        logger.info(
            "evaluation_backlog.date_refreshed",
            extra={"match_date": match_date, "fixture_count": len(fixtures)},
        )
        for fixture in fixtures:
            save_match(fixture)
        saved_rows = {row["match_key"]: row for row in get_day(match_date)}
        for fixture in fixtures:
            row = saved_rows.get(fixture["match_key"])
            if not row or row.get("evaluation") or not fixture.get("actual_result"):
                continue
            outputs = row.get("model_outputs") or {}
            if len(outputs) != 5:
                logger.warning(
                    "evaluation.skipped_incomplete_panel",
                    extra={"match_key": fixture["match_key"], "model_count": len(outputs)},
                )
                continue
            try:
                evaluation = evaluate_analysts(fixture, fixture["actual_result"], outputs)
                save_evaluation(
                    fixture["match_key"], fixture["actual_result"], evaluation, EVALUATION_MODEL
                )
                evaluated += 1
            except Exception as error:
                logger.exception(
                    "evaluation.deferred",
                    extra={"match_key": fixture["match_key"], "error_type": type(error).__name__},
                )
    logger.info(
        "evaluation_backlog.complete",
        extra={
            "run_date": day.isoformat(),
            "evaluated_matches": evaluated,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        },
    )
    return evaluated


def run_for_date(day: date) -> list[dict]:
    """Discover and analyze every fixture for a day; upserts make reruns safe."""
    started = time.perf_counter()
    logger.info("pipeline.run.start", extra={"match_date": day.isoformat()})
    initialize_database()
    evaluated = refresh_and_evaluate_previous(day)
    fixtures = get_fixtures_football_data(day)
    logger.info(
        "pipeline.fixtures.ready",
        extra={"match_date": day.isoformat(), "fixture_count": len(fixtures), "evaluated_matches": evaluated},
    )
    results = []
    for match in fixtures:
        match_started = time.perf_counter()
        logger.info(
            "pipeline.match.start",
            extra={
                "match_key": match["match_key"],
                "home_team": match["home_team"],
                "away_team": match["away_team"],
            },
        )
        save_match(match)
        raw_research = research_match(match)
        outputs = run_analysts(MODELS, analyst_prompt(match, raw_research))
        final = openrouter_chat(
            MASTER_MODEL,
            [{"role": "user", "content": master_prompt(match, raw_research, outputs)}],
            temperature=0.1,
        )
        save_analysis(match["match_key"], {"raw": raw_research}, outputs, final)
        results.append({"match": match, "model_outputs": outputs, "final_output": final})
        logger.info(
            "pipeline.match.complete",
            extra={
                "match_key": match["match_key"],
                "analyst_count": len(outputs),
                "master_output_characters": len(final),
                "elapsed_seconds": round(time.perf_counter() - match_started, 3),
            },
        )
    logger.info(
        "pipeline.run.complete",
        extra={
            "match_date": day.isoformat(),
            "fixture_count": len(fixtures),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        },
    )
    return results
