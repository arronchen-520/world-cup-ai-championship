import json
import logging

from logging_config import JsonFormatter


def test_json_formatter_includes_event_and_structured_fields():
    record = logging.LogRecord(
        name="pipeline", level=logging.INFO, pathname=__file__, lineno=10,
        msg="pipeline.run.complete", args=(), exc_info=None,
    )
    record.match_date = "2026-06-21"
    record.fixture_count = 4
    payload = json.loads(JsonFormatter().format(record))
    assert payload["event"] == "pipeline.run.complete"
    assert payload["match_date"] == "2026-06-21"
    assert payload["fixture_count"] == 4
