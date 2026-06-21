import services
from services import analyst_prompt, openrouter_chat


def test_openrouter_max_tokens_is_sent(monkeypatch):
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
    assert openrouter_chat("test/model", [{"role": "user", "content": "test"}], max_tokens=321) == "ok"
    assert captured["json"]["max_tokens"] == 321


def test_analyst_prompt_is_concise_first_and_independent():
    match = {
        "home_team": "A", "away_team": "B", "competition": "World Cup",
        "match_date": "2026-06-21", "referees": [],
    }
    prompt = analyst_prompt(match, {"searches": []})
    assert prompt.index("## 快速结论") < prompt.index("## 详细分析")
    assert "independent football analysis" in prompt
    assert "不超过 600 个中文字符" in prompt
