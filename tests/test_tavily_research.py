import services
from services import research_match


def test_research_uses_advanced_depth_without_custom_limits(monkeypatch):
    calls = []

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

    monkeypatch.setattr(services, "TAVILY_API_KEY", "test-key")
    monkeypatch.setattr(services, "TavilyClient", lambda api_key: Client())
    match = {
        "home_team": "Spain",
        "away_team": "Saudi Arabia",
        "match_date": "2026-06-21",
        "referees": [],
    }
    research = research_match(match)

    assert {call["search_depth"] for call in calls} == {"advanced"}
    assert all("max_results" not in call for call in calls)
    assert all("include_domains" not in call for call in calls)
    assert [len(search["results"][0]["content"]) for search in research["searches"]] == [5000, 5000, 5000]
    assert [search["category"] for search in research["searches"]] == [
        "team_news_form_h2h", "betting_markets", "tactics_venue_weather_referee",
    ]
    assert all(len(search["query"]) < 400 for search in research["searches"])
    assert "weather" not in research["searches"][2]["query"]
