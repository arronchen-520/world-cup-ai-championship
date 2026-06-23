import services
from services import analyst_prompt, odds_research_for_match, research_match


def test_research_uses_shared_advanced_depth_and_result_count(monkeypatch):
    calls = []
    odds_calls = []

    class Client:
        def search(self, **kwargs):
            calls.append(kwargs)
            return {
                "results": [{
                    "title": "Relevant result",
                    "url": "https://sports.yahoo.com/result",
                    "content": "x" * 5000,
                }]
            }

    class OddsResponse:
        def __init__(self, payload, requests_last):
            self.payload = payload
            self.headers = {
                "x-requests-last": requests_last,
                "x-requests-used": "12",
                "x-requests-remaining": "488",
            }

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    base_event = {
                "id": "event-1",
                "home_team": "Spain",
                "away_team": "Saudi Arabia",
                "commence_time": "2026-06-21T18:00:00Z",
                "bookmakers": [{
                    "key": "bovada",
                    "title": "Bovada",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Spain", "price": 1.2},
                                {"name": "Draw", "price": 6.0},
                                {"name": "Saudi Arabia", "price": 12.0},
                            ],
                        },
                        {
                            "key": "spreads",
                            "outcomes": [
                                {"name": "Spain", "price": 1.9, "point": -1.5},
                                {"name": "Saudi Arabia", "price": 1.9, "point": 1.5},
                            ],
                        },
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": 1.9, "point": 2.5},
                                {"name": "Under", "price": 1.9, "point": 2.5},
                            ],
                        },
                    ],
                }],
            }
    additional_event = {
        **base_event,
        "bookmakers": [{
            "key": "bovada",
            "title": "Bovada",
            "markets": [
                {
                    "key": "alternate_totals",
                    "last_update": "2026-06-21T10:00:00Z",
                    "outcomes": [
                        {"name": "Over", "price": 1.35, "point": 1.5},
                        {"name": "Under", "price": 3.1, "point": 1.5},
                        {"name": "Over", "price": 2.4, "point": 3.5},
                        {"name": "Under", "price": 1.55, "point": 3.5},
                    ],
                },
                {
                    "key": "btts",
                    "outcomes": [
                        {"name": "Yes", "price": 2.1},
                        {"name": "No", "price": 1.7},
                    ],
                },
                {
                    "key": "alternate_spreads",
                    "outcomes": [
                        {"name": "Spain", "price": 1.65, "point": -2.5},
                        {"name": "Saudi Arabia", "price": 2.2, "point": 2.5},
                        {"name": "Spain", "price": 2.4, "point": -3.5},
                        {"name": "Saudi Arabia", "price": 1.55, "point": 3.5},
                    ],
                },
            ],
        }],
    }

    def fake_get(url, **kwargs):
        odds_calls.append({"url": url, **kwargs})
        if "/events/event-1/odds" in url:
            return OddsResponse(additional_event, "3")
        return OddsResponse([base_event], "3")

    monkeypatch.setattr(services, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(services, "ODDS_API_KEY", "odds-key")
    monkeypatch.setattr(services, "TavilyClient", lambda api_key: Client())
    monkeypatch.setattr(services.httpx, "get", fake_get)
    services._fetch_odds_for_day.cache_clear()
    services._fetch_additional_odds.cache_clear()
    match = {
        "home_team": "Spain",
        "away_team": "Saudi Arabia",
        "match_date": "2026-06-21",
        "referees": [],
    }
    research = research_match(match)

    assert len(calls) == 2
    assert len(odds_calls) == 2
    assert odds_calls[0]["params"]["bookmakers"] == "bovada"
    assert odds_calls[0]["params"]["markets"] == "h2h,spreads,totals"
    assert odds_calls[1]["url"].endswith("/sports/soccer_fifa_world_cup/events/event-1/odds")
    assert odds_calls[1]["params"]["markets"] == "alternate_totals,btts,alternate_spreads"
    assert {call["search_depth"] for call in calls} == {"advanced"}
    assert {call["max_results"] for call in calls} == {5}
    assert all("include_domains" not in call for call in calls)
    assert [len(search["results"][0]["content"]) for search in research["searches"] if "results" in search] == [
        5000, 5000,
    ]
    assert [search["category"] for search in research["searches"]] == [
        "team_news_form_h2h", "betting_markets", "tactics_venue_weather_referee",
    ]
    assert research["searches"][1]["source"] == "the-odds-api.com"
    assert research["searches"][1]["matched_event"]["bookmaker"] == "Bovada"
    assert research["searches"][1]["matched_event"]["available_markets"] == [
        "alternate_spreads", "alternate_totals", "btts", "h2h", "spreads", "totals",
    ]
    assert research["searches"][1]["matched_event"]["markets"]["alternate_spreads"][0] == {
        "name": "Spain",
        "price": 1.65,
        "point": -2.5,
    }
    assert research["searches"][1]["matched_event"]["missing_markets"] == []
    prompt = analyst_prompt(
        {**match, "competition": "World Cup"},
        research,
    )
    assert '"alternate_totals"' in prompt
    assert '"btts"' in prompt
    assert '"alternate_spreads"' in prompt
    assert '"point": -3.5' in prompt
    assert len(prompt) < 20_000
    assert all(len(search["query"]) < 400 for search in research["searches"] if "query" in search)
    assert "weather" not in research["searches"][2]["query"]


def test_additional_odds_failure_keeps_base_markets(monkeypatch):
    base_event = {
        "event_id": "event-1",
        "home_team": "Spain",
        "away_team": "Saudi Arabia",
        "available_markets": ["h2h"],
        "missing_markets": ["spreads", "totals"],
        "markets": {"h2h": [{"name": "Spain", "price": 1.2}]},
        "market_last_updates": {},
    }
    monkeypatch.setattr(services, "_fetch_odds_for_day", lambda day: {
        "provider": "the-odds-api.com",
        "bookmaker": "Bovada",
        "bookmaker_key": "bovada",
        "markets_requested": ["h2h", "spreads", "totals"],
        "odds_format": "decimal",
        "match_date": day,
        "utc_window": {},
        "quota": {},
        "events": [base_event],
    })

    def fail_additional(event_id):
        request = services.httpx.Request("GET", f"https://example.test/{event_id}")
        response = services.httpx.Response(422, request=request)
        raise services.httpx.HTTPStatusError("unsupported markets", request=request, response=response)

    monkeypatch.setattr(services, "_fetch_additional_odds", fail_additional)
    result = odds_research_for_match({
        "home_team": "Spain",
        "away_team": "Saudi Arabia",
        "match_date": "2026-06-21",
    })

    assert result["matched_event"]["markets"] == base_event["markets"]
    assert result["quota"]["additional"] == {}
    assert result["additional_markets_error"] == "HTTP 422"
    assert "apiKey" not in result["additional_markets_error"]


def test_additional_odds_are_cached_by_event(monkeypatch):
    calls = []

    class Response:
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "event-1",
                "home_team": "Spain",
                "away_team": "Saudi Arabia",
                "bookmakers": [],
            }

    def fake_get(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return Response()

    monkeypatch.setattr(services, "ODDS_API_KEY", "odds-key")
    monkeypatch.setattr(services.httpx, "get", fake_get)
    services._fetch_additional_odds.cache_clear()

    first = services._fetch_additional_odds("event-1")
    second = services._fetch_additional_odds("event-1")

    assert first is second
    assert len(calls) == 1
