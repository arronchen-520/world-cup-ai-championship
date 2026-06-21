"""External API clients and prompt construction."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from typing import Annotated, Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tavily import TavilyClient
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from config import (
    ANALYST_MAX_TOKENS, BETTING_CONTENT_CHARS, BETTING_MAX_RESULTS, CONTEXT_CONTENT_CHARS,
    CONTEXT_MAX_RESULTS, FOOTBALL_DATA_API_KEY, FOOTBALL_DATA_COMPETITION, FOOTBALL_DATA_URL,
    MATCH_TIMEZONE, OPENROUTER_API_KEY, OPENROUTER_URL, REQUEST_TIMEOUT_SECONDS,
    RESEARCH_SUMMARY_MAX_TOKENS, RESEARCH_SUMMARY_MODEL, TAVILY_API_KEY, TAVILY_SEARCH_DEPTH,
    TEAM_NEWS_CONTENT_CHARS, TEAM_NEWS_MAX_RESULTS,
)


SOCIAL_DOMAINS = ["instagram.com", "facebook.com", "x.com", "tiktok.com", "youtube.com"]
BETTING_ALLOWED_DOMAINS = [
    "actionnetwork.com",
    "bet365.com",
    "betmgm.com",
    "caesars.com",
    "covers.com",
    "draftkings.com",
    "espn.com",
    "fanduel.com",
    "foxsports.com",
    "ladbrokes.com",
    "oddschecker.com",
    "oddspedia.com",
    "paddypower.com",
    "racingpost.com",
    "sports.yahoo.com",
    "thelines.com",
    "williamhill.com",
]


class EvidencePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str = Field(min_length=1, max_length=160)
    source_url: str | None = Field(default=None, max_length=600)


class BettingPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    market: str = Field(min_length=1, max_length=100)
    price_or_probability: str = Field(min_length=1, max_length=120)
    source_url: str | None = Field(default=None, max_length=600)
    caveat: str | None = Field(default=None, max_length=120)


DigestGap = Annotated[str, Field(min_length=1, max_length=160)]


class ResearchDigest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_news_form_h2h: list[EvidencePoint] = Field(default_factory=list, max_length=5)
    betting_markets: list[BettingPoint] = Field(default_factory=list, max_length=6)
    tactics_venue_weather: list[EvidencePoint] = Field(default_factory=list, max_length=3)
    conflicts_and_gaps: list[DigestGap] = Field(default_factory=list, max_length=4)


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
    max_tokens: int | None = None,
    response_format: dict[str, Any] | None = None,
) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not configured")
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
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
    search_specs = [
        {
            "query": f"{fixture} latest team news injuries suspensions predicted lineups last 5 matches head to head",
            "max_results": TEAM_NEWS_MAX_RESULTS,
            "content_chars": TEAM_NEWS_CONTENT_CHARS,
        },
        {
            "query": (
                f"{fixture} latest bookmaker odds 1X2 double chance draw no bet Asian handicap "
                "totals over under 1.5 2.5 3.5 both teams to score"
            ),
            "max_results": BETTING_MAX_RESULTS,
            "content_chars": BETTING_CONTENT_CHARS,
            "allowed_domains": BETTING_ALLOWED_DOMAINS,
        },
        {
            "query": f"{fixture} tactical preview venue weather referee {referee_names}".strip(),
            "max_results": CONTEXT_MAX_RESULTS,
            "content_chars": CONTEXT_CONTENT_CHARS,
        },
    ]

    def search(spec: dict[str, Any]) -> dict[str, Any]:
        search_args: dict[str, Any] = {
            "query": spec["query"],
            "search_depth": TAVILY_SEARCH_DEPTH,
            "max_results": spec["max_results"],
            "include_answer": False,
            "exclude_domains": SOCIAL_DOMAINS,
        }
        if spec.get("allowed_domains"):
            search_args["include_domains"] = spec["allowed_domains"]
        response = client.search(
            **search_args,
        )
        raw_results = response.get("results", [])
        if spec.get("allowed_domains"):
            raw_results = [
                result for result in raw_results
                if any(
                    (hostname := (urlparse(result.get("url") or "").hostname or "").lower()) == domain
                    or hostname.endswith(f".{domain}")
                    for domain in spec["allowed_domains"]
                )
            ]
        return {
            "results": [
                {
                    "title": result.get("title"),
                    "url": result.get("url"),
                    "score": result.get("score"),
                    "content": (result.get("content") or "")[:spec["content_chars"]],
                }
                for result in raw_results
            ]
        }

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(search, search_specs))
    return {"queries": [spec["query"] for spec in search_specs], "searches": results}


def summarize_research(match: dict[str, Any], research: dict[str, Any]) -> dict[str, Any]:
    """Compress Tavily evidence once, then validate the shared digest with Pydantic."""
    schema = ResearchDigest.model_json_schema()
    prompt = f"""Summarize the search evidence for {match['home_team']} vs {match['away_team']}.
Return one JSON object matching the supplied schema. Use Simplified Chinese for claims.
Preserve source URLs, quoted odds/lines, and uncertainty. Do not infer missing current facts.
For head-to-head, note when old meetings have limited relevance. For betting, capture all available
1X2, double-chance, draw-no-bet, Asian handicap, BTTS, and totals 1.5/2.5/3.5 markets.
Keep only decision-relevant evidence and explicitly list contradictions or missing markets.

JSON schema:
{json.dumps(schema, ensure_ascii=False)}

Search evidence:
{json.dumps(research, ensure_ascii=False)}"""
    response = openrouter_chat(
        RESEARCH_SUMMARY_MODEL,
        [{"role": "user", "content": prompt}],
        temperature=0,
        max_tokens=RESEARCH_SUMMARY_MAX_TOKENS,
        response_format={"type": "json_object"},
    ).strip()
    if response.startswith("```"):
        response = response.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    return ResearchDigest.model_validate_json(response).model_dump(mode="json")


def analyst_prompt(match: dict[str, Any], research_digest: dict[str, Any]) -> str:
    return f"""You are one independent football analyst in an AI championship.
Analyze {match['home_team']} vs {match['away_team']} in {match['competition']} on {match['match_date']}.
Fixture details: group={match.get('group_name') or 'unknown'}, kickoff={match.get('kickoff') or 'unknown'},
venue={match.get('venue') or 'unknown'}, referees={json.dumps(match.get('referees', []), ensure_ascii=False)}.

Use the supplied evidence digest as current evidence, but also add your own independent football analysis,
tactical reasoning, and general knowledge. Clearly distinguish retrieved facts from your own analysis.
Never invent current injuries, lineups, or odds. Flag stale/conflicting information and account for bookmaker margin.

Return concise Markdown in exactly this order:
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
快速结论不超过 180 个中文字符；详细分析不超过 600 个中文字符。
Write the entire response in Simplified Chinese, including all headings and explanations.

Research digest:
{json.dumps(research_digest, ensure_ascii=False)}"""


def run_analysts(models: list[dict[str, str]], prompt: str) -> dict[str, str]:
    outputs: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(5, len(models))) as pool:
        futures = {
            pool.submit(
                openrouter_chat,
                model["model"],
                [{"role": "user", "content": prompt}],
                0.2,
                ANALYST_MAX_TOKENS,
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


def master_prompt(match: dict[str, Any], research_digest: dict[str, Any], outputs: dict[str, str]) -> str:
    return f"""Act as the chair of a football prediction panel. Synthesize the independent reports below.
Do not decide by majority vote alone: weigh source quality, reasoning, market prices, injuries, and disagreement.
Never invent facts or odds. If there is no defensible edge, explicitly recommend No bet.

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
最终结论不超过 220 个中文字符；综合分析不超过 800 个中文字符。
Write the entire final recommendation in Simplified Chinese.

Match: {json.dumps(match, default=str)}
Research digest: {json.dumps(research_digest, ensure_ascii=False)}
Panel reports: {json.dumps(outputs, ensure_ascii=False)}"""
