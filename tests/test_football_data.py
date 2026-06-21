from datetime import date

import services
from services import extract_actual_result, get_fixtures_football_data


PAYLOAD = {
    "matches": [{
        "id": 123,
        "utcDate": "2026-06-20T17:00:00Z",
        "venue": "NRG Stadium",
        "stage": "GROUP_STAGE",
        "group": "GROUP_F",
        "competition": {"name": "FIFA World Cup"},
        "homeTeam": {"name": "Netherlands"},
        "awayTeam": {"name": "Sweden"},
        "referees": [{"name": "Jane Referee", "type": "REFEREE", "nationality": "Canada"}],
    }]
}


def test_extracts_regulation_result_before_extra_time():
    result = extract_actual_result({
        "status": "FINISHED",
        "score": {
            "duration": "EXTRA_TIME",
            "winner": "HOME_TEAM",
            "regularTime": {"home": 1, "away": 1},
            "fullTime": {"home": 2, "away": 1},
            "extraTime": {"home": 1, "away": 0},
            "penalties": {"home": None, "away": None},
        },
    })
    assert result["regulation_outcome"] == "DRAW"
    assert (result["regulation_home"], result["regulation_away"]) == (1, 1)
    assert extract_actual_result({"status": "TIMED", "score": {}}) is None


def test_fixture_request_and_normalization(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            boundary_match = PAYLOAD["matches"][0] | {
                "id": 124,
                "utcDate": "2026-06-21T04:00:00Z",
            }
            next_day_match = PAYLOAD["matches"][0] | {
                "id": 125,
                "utcDate": "2026-06-21T05:00:00Z",
            }
            previous_day_match = PAYLOAD["matches"][0] | {
                "id": 126,
                "utcDate": "2026-06-20T03:00:00Z",
            }
            return {"matches": [*PAYLOAD["matches"], boundary_match, next_day_match, previous_day_match]}

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(services, "FOOTBALL_DATA_API_KEY", "test-token")
    monkeypatch.setattr(services.httpx, "get", fake_get)
    fixtures = get_fixtures_football_data(date(2026, 6, 20))

    assert captured["headers"] == {"X-Auth-Token": "test-token"}
    assert captured["params"] == {"dateFrom": "2026-06-20", "dateTo": "2026-06-21"}
    assert fixtures[0]["match_key"] == "football-data:123"
    assert fixtures[0]["group_name"] == "Group F"
    assert fixtures[0]["referees"][0]["name"] == "Jane Referee"
    assert [fixture["match_key"] for fixture in fixtures] == ["football-data:123", "football-data:124"]
    assert fixtures[1]["kickoff_local"].startswith("2026-06-20T23:00:00")
