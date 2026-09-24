"""Request correlation metadata is included and cannot leak across contexts."""

import json
import logging

from src.logging_config import JSONFormatter, RequestContextFilter
from src.request_context import request_id


def test_request_id_filter_and_json_formatter():
    token = request_id.set("request-123")
    try:
        record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
        assert RequestContextFilter().filter(record)
        assert json.loads(JSONFormatter().format(record))["request_id"] == "request-123"
    finally:
        request_id.reset(token)

    next_record = logging.LogRecord("test", logging.INFO, __file__, 1, "hello", (), None)
    RequestContextFilter().filter(next_record)
    assert next_record.request_id == "-"
