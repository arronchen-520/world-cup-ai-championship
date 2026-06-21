"""End-to-end daily analysis orchestration."""

from __future__ import annotations

from datetime import date, timedelta

from config import EVALUATION_MODEL, MASTER_MAX_TOKENS, MASTER_MODEL, MAX_MATCHES_PER_RUN, MODELS
from database import (
    get_day, get_pending_evaluation_dates, initialize_database,
    save_analysis, save_evaluation, save_match,
)
from services import (
    analyst_prompt, evaluate_analysts, get_fixtures_football_data, master_prompt,
    openrouter_chat, research_match, run_analysts, summarize_research,
)


def refresh_and_evaluate_previous(day: date) -> int:
    """Refresh yesterday plus unfinished backlog and score newly finished matches."""
    yesterday = (day - timedelta(days=1)).isoformat()
    pending_dates = get_pending_evaluation_dates(day.isoformat())
    dates_to_refresh = list(dict.fromkeys([yesterday, *pending_dates]))
    evaluated = 0
    for match_date in dates_to_refresh:
        fixtures = get_fixtures_football_data(date.fromisoformat(match_date))
        for fixture in fixtures:
            save_match(fixture)
        saved_rows = {row["match_key"]: row for row in get_day(match_date)}
        for fixture in fixtures:
            row = saved_rows.get(fixture["match_key"])
            if not row or row.get("evaluation") or not fixture.get("actual_result"):
                continue
            outputs = row.get("model_outputs") or {}
            if len(outputs) != 5:
                continue
            try:
                evaluation = evaluate_analysts(fixture, fixture["actual_result"], outputs)
                save_evaluation(
                    fixture["match_key"], fixture["actual_result"], evaluation, EVALUATION_MODEL
                )
                evaluated += 1
            except Exception as error:
                print(
                    f"Evaluation deferred for {fixture['match_key']}: "
                    f"{type(error).__name__}: {error}"
                )
    return evaluated


def run_for_date(day: date) -> list[dict]:
    """Discover and analyze every fixture for a day; upserts make reruns safe."""
    initialize_database()
    evaluated = refresh_and_evaluate_previous(day)
    print(f"Added {evaluated} completed-match evaluation(s).")
    fixtures = get_fixtures_football_data(day)
    fixtures = fixtures[:MAX_MATCHES_PER_RUN]
    results = []
    for match in fixtures:
        save_match(match)
        raw_research = research_match(match)
        try:
            research_digest = summarize_research(match, raw_research)
            research_for_models = research_digest
        except Exception as error:
            research_digest = {
                "summary_status": "fallback_raw",
                "summary_error": type(error).__name__,
            }
            research_for_models = raw_research
        outputs = run_analysts(MODELS, analyst_prompt(match, research_for_models))
        final = openrouter_chat(
            MASTER_MODEL,
            [{"role": "user", "content": master_prompt(match, research_for_models, outputs)}],
            temperature=0.1,
            max_tokens=MASTER_MAX_TOKENS,
        )
        stored_research = {"raw": raw_research, "digest": research_digest}
        save_analysis(match["match_key"], stored_research, outputs, final)
        results.append({"match": match, "model_outputs": outputs, "final_output": final})
    return results
