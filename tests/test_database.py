from pathlib import Path

from database import get_day, initialize_database, save_analysis, save_match


def test_round_trip(tmp_path: Path):
    path = tmp_path / "test.db"
    initialize_database(path)
    match = {
        "match_key": "test:1", "match_date": "2026-06-20", "kickoff": "2026-06-20T19:00:00Z",
        "kickoff_local": "2026-06-20T15:00:00-04:00",
        "competition": "World Cup", "home_team": "Alpha", "away_team": "Beta",
        "group_name": "Group A", "venue": "National Stadium",
        "referees": [{"name": "Jane Referee", "role": "REFEREE", "nationality": "Canada"}],
        "source": "test", "raw": {"fixture": 1},
    }
    save_match(match, path)
    save_analysis("test:1", {"source": "research"}, {"model": "opinion"}, "final", path)
    rows = get_day("2026-06-20", path)
    assert len(rows) == 1
    assert rows[0]["home_team"] == "Alpha"
    assert rows[0]["venue"] == "National Stadium"
    assert rows[0]["kickoff_local"] == "2026-06-20T15:00:00-04:00"
    assert rows[0]["referees"][0]["name"] == "Jane Referee"
    assert rows[0]["model_outputs"] == {"model": "opinion"}
    assert rows[0]["final_output"] == "final"
