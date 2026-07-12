from pathlib import Path

from database import (
    get_day, get_leaderboard, initialize_database, save_analysis, save_evaluation, save_match,
)


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


def test_evaluation_updates_standings(tmp_path: Path):
    path = tmp_path / "scores.db"
    initialize_database(path)
    match = {
        "match_key": "test:2", "match_date": "2026-06-20", "kickoff": None,
        "competition": "World Cup", "home_team": "Alpha", "away_team": "Beta",
        "referees": [], "source": "test", "raw": {},
        "status": "FINISHED",
        "actual_result": {"regulation_home": 2, "regulation_away": 1, "regulation_outcome": "HOME_WIN"},
    }
    save_match(match, path)
    save_analysis("test:2", {}, {f"model-{i}": "output" for i in range(1, 6)}, "master", path)
    ranking = [
        {"model_id": f"model-{rank}", "rank": rank, "points": 6 - rank, "reason": "reason"}
        for rank in range(1, 6)
    ]
    save_evaluation(
        "test:2", match["actual_result"],
        {"ranking": ranking, "overall_analysis": "overall"}, "deepseek", path,
    )

    row = get_day("2026-06-20", path)[0]
    assert row["evaluation"]["ranking"][0]["points"] == 5
    standings = get_leaderboard(path)
    assert standings[0]["model_id"] == "model-1"
    assert standings[0]["average_score"] == 5.0


def test_initialize_database_migrates_gpt_55_history(tmp_path: Path):
    path = tmp_path / "legacy.db"
    initialize_database(path)
    match = {
        "match_key": "test:legacy", "match_date": "2026-06-20", "kickoff": None,
        "competition": "World Cup", "home_team": "Alpha", "away_team": "Beta",
        "referees": [], "source": "test", "raw": {}, "status": "FINISHED",
        "actual_result": {"regulation_home": 1, "regulation_away": 0, "regulation_outcome": "HOME_WIN"},
    }
    save_match(match, path)
    outputs = {"gpt-5.5": "legacy opinion", **{f"model-{i}": "output" for i in range(2, 6)}}
    save_analysis("test:legacy", {}, outputs, "master", path)
    ranking = [
        {"model_id": model_id, "rank": rank, "points": 6 - rank, "reason": "reason"}
        for rank, model_id in enumerate(outputs, start=1)
    ]
    save_evaluation(
        "test:legacy", match["actual_result"],
        {"ranking": ranking, "overall_analysis": "overall"}, "openai/gpt-5.5", path,
    )

    initialize_database(path)

    row = get_day("2026-06-20", path)[0]
    assert "gpt-5.5" not in row["model_outputs"]
    assert row["model_outputs"]["gpt-5.6-sol"] == "legacy opinion"
    assert row["evaluation"]["ranking"][0]["model_id"] == "gpt-5.6-sol"
    assert row["evaluation"]["evaluator_model"] == "openai/gpt-5.6-sol"
    assert {item["model_id"] for item in get_leaderboard(path)} == {
        "gpt-5.6-sol", "model-2", "model-3", "model-4", "model-5",
    }
