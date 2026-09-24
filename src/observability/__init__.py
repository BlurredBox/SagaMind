"""
SagaMind Observability
======================

Prometheus metrics and optional OpenTelemetry tracing. Both degrade to no-ops when their
libraries are not installed, so importing this package never fails and adds no hard runtime
dependency.
"""

from src.observability.metrics import metrics, span

__all__ = ["metrics", "span"]
