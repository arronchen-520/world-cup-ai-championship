"""End-to-end daily analysis orchestration."""

from __future__ import annotations

from datetime import date

from config import MASTER_MAX_TOKENS, MASTER_MODEL, MAX_MATCHES_PER_RUN, MODELS
from database import initialize_database, save_analysis, save_match
from services import (
    analyst_prompt, get_fixtures_football_data, master_prompt,
    openrouter_chat, research_match, run_analysts, summarize_research,
)


def run_for_date(day: date) -> list[dict]:
    """Discover and analyze every fixture for a day; upserts make reruns safe."""
    initialize_database()
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
