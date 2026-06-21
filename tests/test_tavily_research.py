import services
from services import research_match


def test_research_uses_basic_depth_and_category_limits(monkeypatch):
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

    assert {call["search_depth"] for call in calls} == {"basic"}
    assert sorted(call["max_results"] for call in calls) == [3, 4, 5]
    assert sum("include_domains" in call for call in calls) == 1
    assert [len(search["results"][0]["content"]) for search in research["searches"]] == [1200, 1600, 800]
    assert all(len(query) < 400 for query in research["queries"])
