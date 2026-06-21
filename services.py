"""External API clients and prompt construction."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tavily import TavilyClient
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config import (
    BETTING_SEARCH_DEPTH, CONTEXT_SEARCH_DEPTH, EVALUATION_MODEL, FOOTBALL_DATA_API_KEY,
    FOOTBALL_DATA_COMPETITION, FOOTBALL_DATA_URL, MATCH_TIMEZONE, OPENROUTER_API_KEY,
    OPENROUTER_URL, REQUEST_TIMEOUT_SECONDS, TAVILY_API_KEY, TEAM_SEARCH_DEPTH,
)


SOCIAL_DOMAINS = ["instagram.com", "facebook.com", "x.com", "tiktok.com", "youtube.com"]


class RankedAnalyst(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str = Field(min_length=1)
    rank: int = Field(ge=1, le=5)
    reason: str = Field(min_length=1)


class AnalystEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ranking: list[RankedAnalyst] = Field(min_length=5, max_length=5)
    overall_analysis: str = Field(min_length=1)


def _retryable_http_error(error: BaseException) -> bool:
    if isinstance(error, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 429} or error.response.status_code >= 500
    return False


@retry(
    retry=retry_if_exception(_retryable_http_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=8),
    reraise=True,
)
def openrouter_chat(
    model: str,
    messages: list[dict[str, str]],
    temperature: float = 0.2,
    response_format: dict[str, Any] | None = None,
) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if response_format is not None:
        payload["response_format"] = response_format
    response = httpx.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {OPENROUTER_API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/ai-world-cup-championship",
            "X-Title": "AI World Cup Championship",
        },
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def get_fixtures_football_data(day: date) -> list[dict[str, Any]]:
    """Fetch one World Cup day from football-data.org and normalize its matches."""
    if not FOOTBALL_DATA_API_KEY:
        raise RuntimeError("FOOTBALL_DATA_API_KEY is not configured")
    local_zone = ZoneInfo(MATCH_TIMEZONE)
    utc_start = datetime.combine(day, time.min, tzinfo=local_zone).astimezone(timezone.utc)
    utc_end = (datetime.combine(day, time.min, tzinfo=local_zone) + timedelta(days=1)).astimezone(timezone.utc)
    response = httpx.get(
        f"{FOOTBALL_DATA_URL}/competitions/{FOOTBALL_DATA_COMPETITION}/matches",
        headers={"X-Auth-Token": FOOTBALL_DATA_API_KEY},
        params={"dateFrom": utc_start.date().isoformat(), "dateTo": utc_end.date().isoformat()},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    fixtures = []
    for match in payload.get("matches", []):
        kickoff = datetime.fromisoformat(match["utcDate"].replace("Z", "+00:00"))
        if not utc_start <= kickoff < utc_end:
            continue
        group_name = match.get("group") or match.get("stage") or ""
        actual_result = extract_actual_result(match)
        fixtures.append({
            "match_key": f"football-data:{match['id']}",
            "match_date": day.isoformat(),
            "kickoff": kickoff.isoformat(),
            "kickoff_local": kickoff.astimezone(local_zone).isoformat(),
            "competition": match.get("competition", {}).get("name", "FIFA World Cup"),
            "group_name": group_name.replace("_", " ").title() or None,
            "home_team": match["homeTeam"]["name"],
            "away_team": match["awayTeam"]["name"],
            "venue": match.get("venue"),
            "status": match.get("status"),
            "actual_result": actual_result,
            "referees": [
                {
                    "name": referee.get("name"),
                    "role": referee.get("type"),
                    "nationality": referee.get("nationality"),
                }
                for referee in match.get("referees", [])
            ],
            "source": "football-data.org",
            "raw": match,
        })
    return fixtures


def extract_actual_result(raw_match: dict[str, Any]) -> dict[str, Any] | None:
    """Return a regulation-time result only when football-data marks the match finished."""
    if raw_match.get("status") != "FINISHED":
        return None
    score = raw_match.get("score") or {}
    regular = score.get("regularTime") or {}
    full_time = score.get("fullTime") or {}
    home = regular.get("home")
    away = regular.get("away")
    if home is None or away is None:
        home, away = full_time.get("home"), full_time.get("away")
    if home is None or away is None:
        return None
    if home > away:
        outcome = "HOME_WIN"
    elif home < away:
        outcome = "AWAY_WIN"
    else:
        outcome = "DRAW"
    return {
        "regulation_home": home,
        "regulation_away": away,
        "regulation_outcome": outcome,
        "duration": score.get("duration"),
        "final_winner": score.get("winner"),
        "full_time": full_time,
        "extra_time": score.get("extraTime"),
        "penalties": score.get("penalties"),
    }


def research_match(match: dict[str, Any]) -> dict[str, Any]:
    if not TAVILY_API_KEY:
        raise RuntimeError("TAVILY_API_KEY is not configured")
    client = TavilyClient(api_key=TAVILY_API_KEY)
    home, away, day = match["home_team"], match["away_team"], match["match_date"]
    display_date = date.fromisoformat(day).strftime("%B %d, %Y")
    referee_names = ", ".join(
        referee.get("name", "") for referee in match.get("referees", []) if referee.get("name")
    )
    fixture = f"{home} vs {away} FIFA World Cup 2026 {display_date}"
    context_terms = "tactical preview"
    if match.get("venue"):
        context_terms += f" venue {match['venue']} weather"
    if referee_names:
        context_terms += f" referee {referee_names}"
    search_specs = [
        {
            "category": "team_news_form_h2h",
            "query": f"{fixture} latest team news injuries suspensions predicted lineups last 5 matches head to head",
            "search_depth": TEAM_SEARCH_DEPTH,
        },
        {
            "category": "betting_markets",
            "query": (
                f"{fixture} latest bookmaker odds 1X2 double chance draw no bet Asian handicap "
                "totals over under 1.5 2.5 3.5 both teams to score"
            ),
            "search_depth": BETTING_SEARCH_DEPTH,
        },
        {
            "category": "tactics_venue_weather_referee",
            "query": f"{fixture} {context_terms}",
            "search_depth": CONTEXT_SEARCH_DEPTH,
        },
    ]

    def search(spec: dict[str, Any]) -> dict[str, Any]:
        search_args: dict[str, Any] = {
            "query": spec["query"],
            "search_depth": spec["search_depth"],
            "include_answer": False,
            "exclude_domains": SOCIAL_DOMAINS,
        }
        response = client.search(
            **search_args,
        )
        raw_results = response.get("results", [])
        return {
            "category": spec["category"],
            "query": spec["query"],
            "results": [
                {
                    "title": result.get("title"),
                    "url": result.get("url"),
                    "score": result.get("score"),
                    "content": result.get("content") or "",
                }
                for result in raw_results
            ]
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(search, search_specs))
    return {"searches": results}


def analyst_prompt(match: dict[str, Any], research: dict[str, Any]) -> str:
    return f"""You are one independent football analyst in an AI championship.
Analyze {match['home_team']} vs {match['away_team']} in {match['competition']} on {match['match_date']}.
Fixture details: group={match.get('group_name') or 'unknown'}, kickoff={match.get('kickoff') or 'unknown'},
venue={match.get('venue') or 'unknown'}, referees={json.dumps(match.get('referees', []), ensure_ascii=False)}.

Use the supplied Tavily results as current evidence, but also add your own independent football analysis,
tactical reasoning, and general knowledge. Clearly distinguish retrieved facts from your own analysis.
Never invent current injuries, lineups, or odds. Flag stale/conflicting information and account for bookmaker margin.
Treat every result and predicted score as regulation time only, excluding extra time and penalties.

Return Markdown in exactly this order:
## 快速结论
- 赛果：主胜 / 平局 / 客胜
- 预测比分：
- 主胜 / 平局 / 客胜概率：total must equal 100%
- 保守投注：
- 进取投注：没有价值时写“无”
- 信心：0-100

## 详细分析
### Tavily 证据
### 独立分析
### 主要风险
When evidence is weak, recommend “不下注”. Put the short actionable answer before all rationale.
Write the entire response in Simplified Chinese, including all headings and explanations.

Research results:
{json.dumps(research, ensure_ascii=False)}"""


def run_analysts(models: list[dict[str, str]], prompt: str) -> dict[str, str]:
    outputs: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(5, len(models))) as pool:
        futures = {
            pool.submit(
                openrouter_chat,
                model["model"],
                [{"role": "user", "content": prompt}],
                0.2,
            ): model
            for model in models
        }
        for future in as_completed(futures):
            model = futures[future]
            try:
                outputs[model["id"]] = future.result()
            except Exception as error:  # Preserve other opinions if one provider fails.
                outputs[model["id"]] = f"Model unavailable: {type(error).__name__}: {error}"
    return outputs


def master_prompt(match: dict[str, Any], research: dict[str, Any], outputs: dict[str, str]) -> str:
    return f"""Act as the chair of a football prediction panel. Synthesize the independent reports below.
Do not decide by majority vote alone: weigh source quality, reasoning, market prices, injuries, and disagreement.
Never invent facts or odds. If there is no defensible edge, explicitly recommend No bet.
Treat every result and predicted score as regulation time only, excluding extra time and penalties.

Return Markdown in exactly this order:
## 最终结论
- 赛果：
- 预测比分：
- 主胜 / 平局 / 客胜概率：total must equal 100%
- 保守投注：
- 进取投注：没有价值时写“无”
- 最终信心：0-100

## 综合分析
Explain model agreement/disagreement, the strongest Tavily evidence, your own independent synthesis,
and invalidation risks. End with a short responsible-gambling notice. Betting is entertainment, not income.
If there is no defensible edge, explicitly recommend “不下注”. Put the final actionable answer first.
Write the entire final recommendation in Simplified Chinese.

Match: {json.dumps(match, default=str)}
Research results: {json.dumps(research, ensure_ascii=False)}
Panel reports: {json.dumps(outputs, ensure_ascii=False)}"""


def evaluate_analysts(
    match: dict[str, Any],
    actual_result: dict[str, Any],
    outputs: dict[str, str],
) -> dict[str, Any]:
    """Rank exactly five analyst reports against the finished regulation-time result."""
    if len(outputs) != 5:
        raise ValueError(f"Expected exactly five analyst outputs, received {len(outputs)}")
    schema = AnalystEvaluation.model_json_schema()
    prompt = f"""You are the post-match judge for an AI football prediction championship.
Evaluate exactly five analyst reports against the actual regulation-time result. Do not evaluate or mention
the master recommendation. Rank all five analysts strictly from 1 (best) to 5 (worst), with no ties.

Weight the judgment as follows:
- 45%: correct regulation-time outcome and closeness of predicted score
- 25%: probability calibration, especially probability assigned to the actual outcome
- 20%: whether conservative/aggressive bets would have settled successfully from the known score
- 10%: reasoning quality, uncertainty handling, and avoidance of unsupported claims

Do not reward verbosity. Do not assume a bet won when its line or settlement cannot be determined.
Return one JSON object matching the schema. Write reasons and overall analysis in Simplified Chinese.

Match: {match['home_team']} vs {match['away_team']}
Actual result: {json.dumps(actual_result, ensure_ascii=False)}
JSON schema: {json.dumps(schema, ensure_ascii=False)}
Analyst reports: {json.dumps(outputs, ensure_ascii=False)}"""
    response = openrouter_chat(
        EVALUATION_MODEL,
        [{"role": "user", "content": prompt}],
        temperature=0,
        response_format={"type": "json_object"},
    ).strip()
    if response.startswith("```"):
        response = response.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    evaluation = AnalystEvaluation.model_validate_json(response)
    expected_models = set(outputs)
    ranked_models = {item.model_id for item in evaluation.ranking}
    ranks = {item.rank for item in evaluation.ranking}
    if ranked_models != expected_models or ranks != {1, 2, 3, 4, 5}:
        raise ValueError("Evaluation must rank each supplied model exactly once from 1 through 5")
    result = evaluation.model_dump(mode="json")
    for item in result["ranking"]:
        item["points"] = 6 - item["rank"]
    return result
