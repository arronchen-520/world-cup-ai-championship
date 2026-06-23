import services
from services import research_match


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
        headers = {
            "x-requests-last": "3",
            "x-requests-used": "12",
            "x-requests-remaining": "488",
        }

        def raise_for_status(self):
            return None

        def json(self):
            return [{
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
            }]

    def fake_get(url, **kwargs):
        odds_calls.append({"url": url, **kwargs})
        return OddsResponse()

    monkeypatch.setattr(services, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(services, "ODDS_API_KEY", "odds-key")
    monkeypatch.setattr(services, "TavilyClient", lambda api_key: Client())
    monkeypatch.setattr(services.httpx, "get", fake_get)
    services._fetch_odds_for_day.cache_clear()
    match = {
        "home_team": "Spain",
        "away_team": "Saudi Arabia",
        "match_date": "2026-06-21",
        "referees": [],
    }
    research = research_match(match)

    assert len(calls) == 2
    assert len(odds_calls) == 1
    assert odds_calls[0]["params"]["bookmakers"] == "bovada"
    assert odds_calls[0]["params"]["markets"] == "h2h,spreads,totals"
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
    assert research["searches"][1]["matched_event"]["available_markets"] == ["h2h", "spreads", "totals"]
    assert all(len(search["query"]) < 400 for search in research["searches"] if "query" in search)
    assert "weather" not in research["searches"][2]["query"]
