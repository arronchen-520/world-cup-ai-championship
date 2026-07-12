from datetime import datetime
from unittest.mock import patch

from app import _choices, _date_string, _evaluation_markdown, _split_output, load_date, show_match


def test_date_string():
    assert _date_string("2026-06-20 00:00:00") == "2026-06-20"
    assert _date_string(datetime(2026, 6, 20, 12)) == "2026-06-20"


def test_choices():
    rows = [{"match_key": "x", "kickoff": None, "kickoff_local": None, "home_team": "A", "away_team": "B"}]
    assert _choices(rows) == [("TBD | A vs B", "x")]


def test_choices_show_time_without_repeating_date():
    rows = [{
        "match_key": "x",
        "kickoff": "2026-06-23T20:00:00Z",
        "kickoff_local": "2026-06-23 15:00",
        "home_team": "A",
        "away_team": "B",
    }]
    assert _choices(rows) == [("15:00 | A vs B", "x")]


def test_load_date_allows_saved_future_matches():
    rows = [{
        "match_key": "future-1", "kickoff": "2099-07-14T20:00:00Z",
        "kickoff_local": "2099-07-14T16:00:00-04:00",
        "home_team": "A", "away_team": "B",
    }]
    with patch("app.get_day", return_value=rows), patch(
        "app._leaderboard_markdown", return_value="leaderboard"
    ):
        dropdown, status, loaded_rows, leaderboard = load_date("2099-07-14")

    assert dropdown["value"] == "future-1"
    assert status == "Found 1 saved match(es) for 2099-07-14."
    assert loaded_rows == rows
    assert leaderboard == "leaderboard"


def test_split_output():
    summary, detail = _split_output("## 快速结论\n主胜\n## 详细分析\n证据", "## 详细分析")
    assert "主胜" in summary
    assert detail == "证据"


def test_evaluation_markdown_is_empty_until_result_exists():
    assert _evaluation_markdown(None) == ""
    text = _evaluation_markdown({
        "actual_result": {
            "regulation_home": 2, "regulation_away": 1, "regulation_outcome": "HOME_WIN",
        },
        "ranking": [{
            "model_id": "model-a", "rank": 1, "points": 5, "reason": "比分最接近",
        }],
        "overall_analysis": "综合复盘",
    })
    assert "2–1" in text
    assert "model-a" in text
    assert "综合复盘" in text


def test_show_match_returns_master_before_analysts():
    row = {
        "match_key": "match-1", "home_team": "A", "away_team": "B",
        "competition": "World Cup", "group_name": None, "kickoff": None,
        "kickoff_local": None, "venue": None, "source": "test", "referees": [],
        "model_outputs": {
            "gpt-5.6-sol": "## 快速结论\nanalyst\n## 详细分析\ndetail",
        },
        "final_output": "## 最终结论\nmaster\n## 综合分析\nmaster detail",
        "evaluation": None,
    }
    outputs = show_match("match-1", [row])
    assert "master" in outputs[1]
    assert outputs[2] == "master detail"
    assert "analyst" in outputs[3]
