import services
from services import analyst_prompt, master_prompt, openrouter_chat


def test_openrouter_does_not_set_an_output_limit(monkeypatch):
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(services, "OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(services.httpx, "post", fake_post)
    assert openrouter_chat("test/model", [{"role": "user", "content": "test"}]) == "ok"
    assert "max_tokens" not in captured["json"]


def test_analyst_prompt_is_concise_first_and_independent():
    match = {
        "home_team": "A", "away_team": "B", "competition": "World Cup",
        "match_date": "2026-06-21", "referees": [],
    }
    prompt = analyst_prompt(match, {"searches": []})
    assert prompt.index("## 快速结论") < prompt.index("## 详细分析")
    assert "independent football analysis" in prompt
    assert "不超过 600 个中文字符" in prompt


def test_analyst_prompt_treats_supplied_odds_as_context_not_limits():
    match = {
        "home_team": "A", "away_team": "B", "competition": "World Cup",
        "match_date": "2026-06-21", "referees": [],
    }
    prompt = analyst_prompt(match, {"searches": []})
    assert "structured odds are context, not a limit" in prompt
    assert "target decimal-odds range" in prompt
    assert "Conservative bet = high-confidence" in prompt
    assert "Aggressive bet = positive-odds/value-seeking" in prompt
    assert "Do not default to \"不下注\" only because the price is short" in prompt
    assert "### 投注候选比较" in prompt


def test_master_prompt_rechecks_betting_candidates():
    match = {
        "home_team": "A", "away_team": "B", "competition": "World Cup",
        "match_date": "2026-06-21", "referees": [],
    }
    prompt = master_prompt(match, {"searches": []}, {"model-a": "进取投注：无"})
    assert "structured odds are context, not a limit" in prompt
    assert "Do not inherit \"无\" mechanically" in prompt
    assert "target decimal-odds range" in prompt
    assert "Conservative bet = high-confidence" in prompt
    assert "Aggressive bet = positive-odds/value-seeking" in prompt


def test_master_prompt_blinds_model_identities_and_keeps_reports():
    match = {
        "match_key": "match-1",
        "home_team": "A",
        "away_team": "B",
        "competition": "World Cup",
        "match_date": "2026-06-21",
        "referees": [],
    }
    outputs = {
        "gpt-5.6-sol": "report from first analyst",
        "claude-sonnet-4.6": "report from second analyst",
        "gemini-3.5-flash": "report from third analyst",
        "grok-4.3": "report from fourth analyst",
        "deepseek-v3": "report from fifth analyst",
    }

    prompt = master_prompt(match, {"searches": []}, outputs)

    assert "Anonymous panel reports:" in prompt
    assert "analyst identities are intentionally hidden" in prompt
    assert "Do not infer model identity" in prompt
    assert all(model_id not in prompt for model_id in outputs)
    assert all(report in prompt for report in outputs.values())
    assert all(f'"Analyst {label}"' in prompt for label in "ABCDE")
    assert prompt == master_prompt(match, {"searches": []}, dict(reversed(outputs.items())))
