from datetime import datetime

from app import _choices, _date_string, _split_output


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
