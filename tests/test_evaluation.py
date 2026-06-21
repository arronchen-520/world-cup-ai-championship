import json

import services
from services import evaluate_analysts


def test_evaluation_validates_ranking_and_assigns_points(monkeypatch):
    outputs = {f"model-{i}": f"prediction {i}" for i in range(1, 6)}
    payload = {
        "ranking": [
            {"model_id": f"model-{rank}", "rank": rank, "reason": f"reason {rank}"}
            for rank in range(1, 6)
        ],
        "overall_analysis": "overall",
    }
    monkeypatch.setattr(services, "openrouter_chat", lambda *args, **kwargs: json.dumps(payload))
    evaluation = evaluate_analysts(
        {"home_team": "A", "away_team": "B"},
        {"regulation_home": 1, "regulation_away": 0, "regulation_outcome": "HOME_WIN"},
        outputs,
    )
    assert [item["points"] for item in evaluation["ranking"]] == [5, 4, 3, 2, 1]
