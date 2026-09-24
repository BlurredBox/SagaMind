"""
SagaMind Metrics & Tracing
==========================

A thin facade over ``prometheus_client`` (metrics) and ``opentelemetry`` (tracing). When
either library is absent the corresponding calls become no-ops, so business code can
instrument unconditionally.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from time import perf_counter
from typing import Any

logger = logging.getLogger("SagaMind.Observability")


class Metrics:
    """Prometheus metrics with graceful no-op fallback."""

    def __init__(self) -> None:
        self.enabled = False
        self._c: dict[str, Any] = {}
        self._h: dict[str, Any] = {}
        try:
            from prometheus_client import Counter, Histogram

            self._c = {
                "sagas_started": Counter("sagamind_sagas_started_total", "Sagas started"),
                "sagas_committed": Counter("sagamind_sagas_committed_total", "Sagas committed"),
                "sagas_rolled_back": Counter("sagamind_sagas_rolled_back_total", "Sagas rolled back"),
                "compensations_failed": Counter(
                    "sagamind_compensations_failed_total", "Compensation failures (inconsistent state)"
                ),
                "steps_rejected": Counter("sagamind_steps_rejected_total", "Steps rejected by the verifier"),
            }
            self._h = {
                "verify_seconds": Histogram("sagamind_verify_seconds", "Verifier latency (s)"),
                "step_seconds": Histogram("sagamind_step_seconds", "Step execution latency (s)"),
            }
            self.enabled = True
            logger.info("Prometheus metrics enabled.")
        except Exception as exc:  # noqa: BLE001 - optional dependency
            logger.debug("prometheus_client unavailable; metrics are no-ops: %s", exc)

    def inc(self, name: str, amount: float = 1.0) -> None:
        if self.enabled and name in self._c:
            self._c[name].inc(amount)

    @contextmanager
    def time(self, name: str) -> Iterator[None]:
        """Observe the duration of the wrapped block into histogram *name*."""
        if not self.enabled or name not in self._h:
            yield
            return
        start = perf_counter()
        try:
            yield
        finally:
            self._h[name].observe(perf_counter() - start)

    def exposition(self) -> tuple[bytes, str]:
        """Return (payload, content_type) for a Prometheus scrape endpoint."""
        try:
            from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

            return generate_latest(), CONTENT_TYPE_LATEST
        except Exception:  # noqa: BLE001
            return (b"# prometheus_client not installed\n", "text/plain; charset=utf-8")


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[None]:
    """OpenTelemetry span context manager; a no-op when OTel is not installed."""
    try:
        from opentelemetry import trace

        tracer = trace.get_tracer("sagamind")
    except Exception:  # noqa: BLE001 - tracing is optional
        context: Any = nullcontext(None)
    else:
        context = tracer.start_as_current_span(name)
    with context as current:
        if current is not None:
            for key, value in attributes.items():
                current.set_attribute(key, value)
        yield


metrics = Metrics()
