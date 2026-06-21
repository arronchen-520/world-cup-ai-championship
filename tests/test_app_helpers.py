from datetime import datetime

from app import _choices, _date_string, _evaluation_markdown, _split_output


def test_date_string():
    assert _date_string("2026-06-20 00:00:00") == "2026-06-20"
    assert _date_string(datetime(2026, 6, 20, 12)) == "2026-06-20"


def test_choices():
    rows = [{"match_key": "x", "kickoff": None, "kickoff_local": None, "home_team": "A", "away_team": "B"}]
    assert _choices(rows) == [("TBD | A vs B", "x")]


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
