"""
SagaMind — Metrics Facade Tests
===============================

The metrics facade must be safe to call whether or not prometheus_client is installed.
"""

import pytest

from src.observability.metrics import Metrics, span


class TestMetricsNoOpSafe:
    def test_inc_unknown_metric_is_safe(self):
        m = Metrics()
        m.inc("does_not_exist")  # must not raise
        m.inc("sagas_started")

    def test_time_contextmanager_runs_block(self):
        m = Metrics()
        ran = []
        with m.time("verify_seconds"):
            ran.append(True)
        with m.time("unknown_histogram"):
            ran.append(True)
        assert ran == [True, True]

    def test_exposition_returns_bytes_and_content_type(self):
        m = Metrics()
        payload, content_type = m.exposition()
        assert isinstance(payload, bytes)
        assert isinstance(content_type, str)


class TestSpan:
    def test_span_is_a_noop_without_otel(self):
        with span("unit-test", attr="value"):
            pass  # must not raise even when opentelemetry is absent

    def test_span_does_not_swallow_body_exception(self):
        with pytest.raises(RuntimeError, match="boom"), span("failing"):
            raise RuntimeError("boom")
