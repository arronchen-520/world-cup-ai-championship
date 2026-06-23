"""External API clients and prompt construction."""

from __future__ import annotations

import json
import logging
import re
import time as clock
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tavily import TavilyClient
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config import (
    EVALUATION_MODEL, FOOTBALL_DATA_API_KEY, FOOTBALL_DATA_COMPETITION, FOOTBALL_DATA_URL,
    MATCH_TIMEZONE, ODDS_API_ADDITIONAL_MARKETS, ODDS_API_BOOKMAKER, ODDS_API_KEY,
    ODDS_API_MARKETS, ODDS_API_SPORT, ODDS_API_URL, OPENROUTER_API_KEY, OPENROUTER_URL,
    REQUEST_TIMEOUT_SECONDS, TAVILY_API_KEY, TAVILY_MAX_RESULTS, TAVILY_SEARCH_DEPTH,
)


SOCIAL_DOMAINS = ["instagram.com", "facebook.com", "x.com", "tiktok.com", "youtube.com"]
logger = logging.getLogger(__name__)

BOOKMAKER_TITLES = {
    "bovada": "Bovada",
}


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


def _safe_http_error(error: httpx.HTTPError) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    return type(error).__name__


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
    started = clock.perf_counter()
    input_characters = sum(len(message.get("content", "")) for message in messages)
    logger.info(
        "openrouter.request.start",
        extra={"model": model, "input_characters": input_characters},
    )
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if response_format is not None:
        payload["response_format"] = response_format
    try:
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
        content = response.json()["choices"][0]["message"]["content"]
    except Exception:
        logger.exception(
            "openrouter.request.failed",
            extra={"model": model, "elapsed_seconds": round(clock.perf_counter() - started, 3)},
        )
        raise
    logger.info(
        "openrouter.request.complete",
        extra={
            "model": model,
            "input_characters": input_characters,
            "output_characters": len(content or ""),
            "elapsed_seconds": round(clock.perf_counter() - started, 3),
        },
    )
    return content


def get_fixtures_football_data(day: date) -> list[dict[str, Any]]:
    """Fetch one World Cup day from football-data.org and normalize its matches."""
    if not FOOTBALL_DATA_API_KEY:
        raise RuntimeError("FOOTBALL_DATA_API_KEY is not configured")
    local_zone = ZoneInfo(MATCH_TIMEZONE)
    utc_start = datetime.combine(day, time.min, tzinfo=local_zone).astimezone(timezone.utc)
    utc_end = (datetime.combine(day, time.min, tzinfo=local_zone) + timedelta(days=1)).astimezone(timezone.utc)
    started = clock.perf_counter()
    logger.info(
        "football_data.fixtures.start",
        extra={"match_date": day.isoformat(), "utc_start": utc_start.isoformat(), "utc_end": utc_end.isoformat()},
    )
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
    logger.info(
        "football_data.fixtures.complete",
        extra={
            "match_date": day.isoformat(),
            "provider_matches": len(payload.get("matches", [])),
            "filtered_matches": len(fixtures),
            "finished_matches": sum(fixture["status"] == "FINISHED" for fixture in fixtures),
            "elapsed_seconds": round(clock.perf_counter() - started, 3),
        },
    )
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


def _odds_day_window(day: str) -> tuple[str, str]:
    local_zone = ZoneInfo(MATCH_TIMEZONE)
    match_day = date.fromisoformat(day)
    local_start = datetime.combine(match_day, time.min, tzinfo=local_zone)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = (local_start + timedelta(days=1)).astimezone(timezone.utc)
    return utc_start.isoformat().replace("+00:00", "Z"), utc_end.isoformat().replace("+00:00", "Z")


def _normalize_team_name(name: str) -> str:
    normalized = name.lower()
    replacements = {
        "united states": "usa",
        "u.s.a.": "usa",
        "u.s.": "usa",
        "korea republic": "south korea",
        "ir iran": "iran",
    }
    for source, target in replacements.items():
        normalized = normalized.replace(source, target)
    return re.sub(r"[^a-z0-9]+", "", normalized)


def _market_keys(value: str) -> list[str]:
    return [market.strip() for market in value.split(",") if market.strip()]


def _simplify_odds_outcome(outcome: dict[str, Any]) -> dict[str, Any]:
    simplified = {
        "name": outcome.get("name"),
        "price": outcome.get("price"),
    }
    if outcome.get("description") is not None:
        simplified["description"] = outcome["description"]
    if outcome.get("point") is not None:
        simplified["point"] = outcome["point"]
    return simplified


def _simplify_bookmaker_markets(bookmaker: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    markets = {}
    market_last_updates = {}
    for market in bookmaker.get("markets", []):
        market_key = market.get("key")
        if not market_key:
            continue
        markets[market_key] = [
            _simplify_odds_outcome(outcome)
            for outcome in market.get("outcomes", [])
        ]
        market_last_updates[market_key] = market.get("last_update")
    return markets, market_last_updates


def _simplify_odds_event(event: dict[str, Any]) -> dict[str, Any]:
    bookmakers = event.get("bookmakers") or []
    bookmaker = next(
        (item for item in bookmakers if item.get("key") == ODDS_API_BOOKMAKER),
        bookmakers[0] if bookmakers else {},
    )
    markets, market_last_updates = _simplify_bookmaker_markets(bookmaker)
    configured_markets = _market_keys(ODDS_API_MARKETS)
    available_markets = sorted(markets)
    return {
        "event_id": event.get("id"),
        "match": f"{event.get('home_team')} vs {event.get('away_team')}",
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "commence_time": event.get("commence_time"),
        "bookmaker": bookmaker.get("title") or BOOKMAKER_TITLES.get(ODDS_API_BOOKMAKER, ODDS_API_BOOKMAKER),
        "available_markets": available_markets,
        "missing_markets": [market for market in configured_markets if market not in available_markets],
        "markets": markets,
        "market_last_updates": market_last_updates,
    }


@lru_cache(maxsize=16)
def _fetch_odds_for_day(day: str) -> dict[str, Any]:
    if not ODDS_API_KEY:
        raise RuntimeError("ODDS_API_KEY is not configured")
    utc_start, utc_end = _odds_day_window(day)
    params = {
        "apiKey": ODDS_API_KEY,
        "bookmakers": ODDS_API_BOOKMAKER,
        "markets": ODDS_API_MARKETS,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
        "commenceTimeFrom": utc_start,
        "commenceTimeTo": utc_end,
    }
    started = clock.perf_counter()
    logger.info(
        "odds_api.request.start",
        extra={
            "match_date": day,
            "sport": ODDS_API_SPORT,
            "bookmaker": ODDS_API_BOOKMAKER,
            "markets": ODDS_API_MARKETS,
        },
    )
    response = httpx.get(
        f"{ODDS_API_URL}/sports/{ODDS_API_SPORT}/odds/",
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    events = [_simplify_odds_event(event) for event in response.json()]
    payload = {
        "provider": "the-odds-api.com",
        "category": "betting_markets",
        "sport": ODDS_API_SPORT,
        "bookmaker": BOOKMAKER_TITLES.get(ODDS_API_BOOKMAKER, ODDS_API_BOOKMAKER),
        "bookmaker_key": ODDS_API_BOOKMAKER,
        "markets_requested": [market.strip() for market in ODDS_API_MARKETS.split(",") if market.strip()],
        "odds_format": "decimal",
        "match_date": day,
        "utc_window": {"start": utc_start, "end": utc_end},
        "quota": {
            "requests_last": response.headers.get("x-requests-last"),
            "requests_used": response.headers.get("x-requests-used"),
            "requests_remaining": response.headers.get("x-requests-remaining"),
        },
        "events": events,
    }
    logger.info(
        "odds_api.request.complete",
        extra={
            "match_date": day,
            "event_count": len(events),
            "requests_last": payload["quota"]["requests_last"],
            "requests_remaining": payload["quota"]["requests_remaining"],
            "elapsed_seconds": round(clock.perf_counter() - started, 3),
        },
    )
    return payload


@lru_cache(maxsize=256)
def _fetch_additional_odds(event_id: str) -> dict[str, Any]:
    requested_markets = _market_keys(ODDS_API_ADDITIONAL_MARKETS)
    if not requested_markets:
        return {
            "markets_requested": [],
            "quota": {},
            "event": None,
        }
    params = {
        "apiKey": ODDS_API_KEY,
        "bookmakers": ODDS_API_BOOKMAKER,
        "markets": ",".join(requested_markets),
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    started = clock.perf_counter()
    logger.info(
        "odds_api.additional_request.start",
        extra={"event_id": event_id, "markets": requested_markets},
    )
    response = httpx.get(
        f"{ODDS_API_URL}/sports/{ODDS_API_SPORT}/events/{event_id}/odds",
        params=params,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    raw_event = response.json()
    if isinstance(raw_event, list):
        raw_event = raw_event[0] if raw_event else {}
    event = _simplify_odds_event(raw_event) if raw_event else None
    payload = {
        "markets_requested": requested_markets,
        "quota": {
            "requests_last": response.headers.get("x-requests-last"),
            "requests_used": response.headers.get("x-requests-used"),
            "requests_remaining": response.headers.get("x-requests-remaining"),
        },
        "event": event,
    }
    logger.info(
        "odds_api.additional_request.complete",
        extra={
            "event_id": event_id,
            "available_markets": event.get("available_markets", []) if event else [],
            "requests_last": payload["quota"]["requests_last"],
            "requests_remaining": payload["quota"]["requests_remaining"],
            "elapsed_seconds": round(clock.perf_counter() - started, 3),
        },
    )
    return payload


def _merge_odds_events(base_event: dict[str, Any], additional_event: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base_event)
    merged["markets"] = {
        **base_event.get("markets", {}),
        **additional_event.get("markets", {}),
    }
    merged["market_last_updates"] = {
        **base_event.get("market_last_updates", {}),
        **additional_event.get("market_last_updates", {}),
    }
    merged["available_markets"] = sorted(merged["markets"])
    all_requested = _market_keys(ODDS_API_MARKETS) + _market_keys(ODDS_API_ADDITIONAL_MARKETS)
    merged["missing_markets"] = [
        market for market in all_requested if market not in merged["available_markets"]
    ]
    return merged


def _match_odds_event(match: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any] | None:
    home = _normalize_team_name(match["home_team"])
    away = _normalize_team_name(match["away_team"])
    for event in events:
        event_home = _normalize_team_name(event.get("home_team") or "")
        event_away = _normalize_team_name(event.get("away_team") or "")
        if {home, away} == {event_home, event_away}:
            return event
    return None


def odds_research_for_match(match: dict[str, Any]) -> dict[str, Any]:
    day_payload = _fetch_odds_for_day(match["match_date"])
    event = _match_odds_event(match, day_payload["events"])
    additional_quota = {}
    additional_error = None
    if event and event.get("event_id") and _market_keys(ODDS_API_ADDITIONAL_MARKETS):
        try:
            additional_payload = _fetch_additional_odds(event["event_id"])
            additional_quota = additional_payload["quota"]
            if additional_payload["event"]:
                event = _merge_odds_events(event, additional_payload["event"])
        except httpx.HTTPError as error:
            additional_error = _safe_http_error(error)
            logger.warning(
                "odds_api.additional_request.failed",
                extra={"event_id": event["event_id"], "error": additional_error},
            )
    candidate_matches = [
        {"event_id": item.get("event_id"), "match": item.get("match"), "commence_time": item.get("commence_time")}
        for item in day_payload["events"]
    ]
    return {
        "category": "betting_markets",
        "source": "the-odds-api.com",
        "provider": day_payload["provider"],
        "bookmaker": day_payload["bookmaker"],
        "bookmaker_key": day_payload["bookmaker_key"],
        "markets_requested": day_payload["markets_requested"],
        "additional_markets_requested": _market_keys(ODDS_API_ADDITIONAL_MARKETS),
        "odds_format": day_payload["odds_format"],
        "match_date": day_payload["match_date"],
        "utc_window": day_payload["utc_window"],
        "quota": {
            "base": day_payload["quota"],
            "additional": additional_quota,
        },
        "additional_markets_error": additional_error,
        "matched_event": event,
        "candidate_matches": candidate_matches if event is None else [],
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
    match_key = match.get("match_key") or f"{home}-vs-{away}-{day}"
    fixture = f"{home} vs {away} FIFA World Cup 2026 {display_date}"
    started = clock.perf_counter()
    logger.info(
        "match.research.start",
        extra={"match_key": match_key, "home_team": home, "away_team": away},
    )
    context_terms = "tactical preview"
    if match.get("venue"):
        context_terms += f" venue {match['venue']} weather"
    if referee_names:
        context_terms += f" referee {referee_names}"
    search_specs = [
        {
            "category": "team_news_form_h2h",
            "query": f"{fixture} latest team news injuries suspensions predicted lineups last 5 matches head to head",
        },
        {
            "category": "tactics_venue_weather_referee",
            "query": f"{fixture} {context_terms}",
        },
    ]

    def search(spec: dict[str, Any]) -> dict[str, Any]:
        search_started = clock.perf_counter()
        logger.info(
            "tavily.search.start",
            extra={
                "match_key": match_key,
                "category": spec["category"],
                "search_depth": TAVILY_SEARCH_DEPTH,
                "max_results": TAVILY_MAX_RESULTS,
                "query_characters": len(spec["query"]),
            },
        )
        search_args: dict[str, Any] = {
            "query": spec["query"],
            "search_depth": TAVILY_SEARCH_DEPTH,
            "max_results": TAVILY_MAX_RESULTS,
            "include_answer": False,
            "exclude_domains": SOCIAL_DOMAINS,
        }
        response = client.search(
            **search_args,
        )
        raw_results = response.get("results", [])
        normalized = {
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
        logger.info(
            "tavily.search.complete",
            extra={
                "match_key": match_key,
                "category": spec["category"],
                "result_count": len(normalized["results"]),
                "content_characters": sum(len(item["content"]) for item in normalized["results"]),
                "elapsed_seconds": round(clock.perf_counter() - search_started, 3),
            },
        )
        return normalized

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(search, search_specs))
    results.insert(1, odds_research_for_match(match))
    research = {"searches": results}
    logger.info(
        "match.research.complete",
        extra={
            "match_key": match_key,
            "research_characters": len(json.dumps(research, ensure_ascii=False)),
            "elapsed_seconds": round(clock.perf_counter() - started, 3),
        },
    )
    return research


def analyst_prompt(match: dict[str, Any], research: dict[str, Any]) -> str:
    return f"""You are one independent football analyst in an AI championship.
Analyze {match['home_team']} vs {match['away_team']} in {match['competition']} on {match['match_date']}.
Fixture details: group={match.get('group_name') or 'unknown'}, kickoff={match.get('kickoff') or 'unknown'},
venue={match.get('venue') or 'unknown'}, referees={json.dumps(match.get('referees', []), ensure_ascii=False)}.

Use the supplied retrieved evidence as current evidence, including Tavily search results and structured odds,
but also add your own independent football analysis, tactical reasoning, and general knowledge. Clearly distinguish retrieved facts from your own analysis.
Never invent current injuries, lineups, or listed odds. Flag stale/conflicting information and account for bookmaker margin.
The supplied structured odds are context, not a limit on valid betting recommendations. You may recommend any football bet type
you believe has value, including markets not listed in the supplied odds, but if no listed price exists you must give a fair
target decimal-odds range such as "only if 1.85+" or "value around 2.10-2.30".
Conservative bet = high-confidence, high-probability angle; low/negative odds are acceptable if the probability is strong.
Aggressive bet = positive-odds/value-seeking angle; prefer plus-money or higher decimal odds with a clear risk/reward case.
Do not default to "不下注" only because the price is short. Use "不下注"/"无" only when you cannot identify a credible
high-probability conservative angle or a credible value-priced aggressive angle after comparing plausible candidates.
Treat every result and predicted score as regulation time only, excluding extra time and penalties.

Return Markdown in exactly this order:
## 快速结论
- 赛果：主胜 / 平局 / 客胜
- 预测比分：
- 主胜 / 平局 / 客胜概率：total must equal 100%
- 保守投注：推荐项 + 当前赔率或目标赔率区间；若不下注，必须给出简短原因
- 进取投注：推荐项 + 当前赔率或目标赔率区间；没有可信价值时才写“无”
- 信心：0-100

## 详细分析
### Tavily 证据
### 独立分析
### 投注候选比较
### 主要风险
When evidence is weak, be cautious, but still separate "no reliable evidence" from "short price". Put the short actionable answer before all rationale.
快速结论不超过 180 个中文字符；详细分析不超过 600 个中文字符。
Write the entire response in Simplified Chinese, including all headings and explanations.

Research results:
{json.dumps(research, ensure_ascii=False)}"""


def run_analysts(models: list[dict[str, str]], prompt: str) -> dict[str, str]:
    started = clock.perf_counter()
    logger.info(
        "analyst_panel.start",
        extra={"model_count": len(models), "prompt_characters": len(prompt)},
    )
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
                logger.info(
                    "analyst.complete",
                    extra={"model_id": model["id"], "output_characters": len(outputs[model["id"]])},
                )
            except Exception as error:  # Preserve other opinions if one provider fails.
                outputs[model["id"]] = f"Model unavailable: {type(error).__name__}: {error}"
                logger.error(
                    "analyst.failed",
                    extra={"model_id": model["id"], "error_type": type(error).__name__},
                )
    logger.info(
        "analyst_panel.complete",
        extra={"model_count": len(models), "elapsed_seconds": round(clock.perf_counter() - started, 3)},
    )
    return outputs


def master_prompt(match: dict[str, Any], research: dict[str, Any], outputs: dict[str, str]) -> str:
    return f"""Act as the chair of a football prediction panel. Synthesize the independent reports below.
Do not decide by majority vote alone: weigh source quality, reasoning, market prices, injuries, and disagreement.
Never invent facts or listed odds. The supplied structured odds are context, not a limit on valid betting recommendations.
You may recommend any football bet type that has a defensible edge; if the bet is not priced in supplied odds, give a fair
target decimal-odds range such as "only if 1.85+" or "value around 2.10-2.30".
Conservative bet = high-confidence, high-probability angle; low/negative odds are acceptable if your probability is strong.
Aggressive bet = positive-odds/value-seeking angle; prefer plus-money or higher decimal odds with a clear risk/reward case.
Do not inherit "无" mechanically from the panel. Re-check the full candidate set and recommend No bet only when there is
no credible high-probability conservative angle or credible value-priced aggressive angle.
Treat every result and predicted score as regulation time only, excluding extra time and penalties.

Return Markdown in exactly this order:
## 最终结论
- 赛果：
- 预测比分：
- 主胜 / 平局 / 客胜概率：total must equal 100%
- 保守投注：推荐项 + 当前赔率或目标赔率区间；若不下注，必须给出简短原因
- 进取投注：推荐项 + 当前赔率或目标赔率区间；没有可信价值时才写“无”
- 最终信心：0-100

## 综合分析
Explain model agreement/disagreement, the strongest retrieved evidence, your own independent synthesis,
and invalidation risks. End with a short responsible-gambling notice. Betting is entertainment, not income.
If there is no defensible edge after comparing plausible conservative and aggressive candidates, explicitly recommend “不下注”.
Put the final actionable answer first.
最终结论不超过 220 个中文字符；综合分析不超过 800 个中文字符。
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
    started = clock.perf_counter()
    match_key = match.get("match_key") or f"{match['home_team']}-vs-{match['away_team']}"
    logger.info(
        "evaluation.start",
        extra={"match_key": match_key, "model_count": len(outputs), "model": EVALUATION_MODEL},
    )
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
每个模型的评价理由不超过 180 个中文字符；综合复盘不超过 800 个中文字符。

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
    logger.info(
        "evaluation.complete",
        extra={
            "match_key": match_key,
            "model": EVALUATION_MODEL,
            "elapsed_seconds": round(clock.perf_counter() - started, 3),
        },
    )
    return result
