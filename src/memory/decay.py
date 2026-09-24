"""
SagaMind Ebbinghaus Memory Decay
================================

Models episodic-memory retention with an Ebbinghaus forgetting curve whose strength is
modulated by salience (importance) and reinforcement (retrieval count):

    R(t) = exp(-t / S_m)
    S_m  = S_init * (1 + gamma * ln(N_access + 1)) * Importance

Timestamps are normalised to UTC so naive/aware mixing can never raise at runtime — a
latent bug that previously broke the dashboard's decay chart.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any


def _as_aware_utc(value: Any) -> datetime:
    """Coerce a timestamp (datetime or ISO-8601 string) into an aware UTC datetime."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    aware: datetime = value.astimezone(timezone.utc)
    return aware


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read *name* from a dataclass/object or a dict, with a default."""
    if hasattr(obj, name):
        return getattr(obj, name)
    if isinstance(obj, dict):
        return obj.get(name, default)
    return default


class EbbinghausMemoryManager:
    """Computes retention values and partitions memories into keep/prune sets."""

    def __init__(self, s_init: float = 12.0, tau: float = 0.15, gamma: float = 0.45):
        self.s_init = s_init  # Base exponential time constant in hours (not half-life)
        self.tau = tau  # Retention threshold below which a memory is evicted
        self.gamma = gamma  # Reinforcement scaling factor

    def _strength(self, importance: float, n_access: int) -> float:
        return self.s_init * (1.0 + self.gamma * math.log1p(max(n_access, 0))) * importance

    def calculate_retention(self, memory: Any, now: datetime | None = None) -> float:
        """Compute the retention probability in [0, 1] for a single memory."""
        now = now or datetime.now(timezone.utc)

        last_ret = _as_aware_utc(_attr(memory, "last_retrieved_at"))
        time_delta_hours = (now - last_ret).total_seconds() / 3600.0

        n_access = int(_attr(memory, "retrieval_count", 0) or 0)
        importance = float(_attr(memory, "importance_score", 0.5))

        strength = self._strength(importance, n_access)
        if strength <= 0:
            return 0.0
        return math.exp(-max(time_delta_hours, 0.0) / strength)

    def evaluate_memories(self, memories: list[Any]) -> tuple[list[Any], list[Any]]:
        """Partition memories into (keep, prune) by the retention threshold ``tau``."""
        now = datetime.now(timezone.utc)
        keep_list, prune_list = [], []
        for m in memories:
            if self.calculate_retention(m, now=now) >= self.tau:
                keep_list.append(m)
            else:
                prune_list.append(m)
        return keep_list, prune_list

    def calculate_retention_batch(self, memories: list[Any]) -> list[float]:
        """Vectorised retention for many memories.

        Falls back to the scalar path when NumPy is unavailable. This is the entry
        point to use when scoring large memory banks; for very large datasets prefer
        computing ``exp(-Δt / S)`` directly in SQL (see ``docs``/``improve.md``).
        """
        if not memories:
            return []
        try:
            import numpy as np
        except ImportError:
            now = datetime.now(timezone.utc)
            return [self.calculate_retention(m, now=now) for m in memories]

        now = datetime.now(timezone.utc)
        deltas = np.array(
            [max((now - _as_aware_utc(_attr(m, "last_retrieved_at"))).total_seconds() / 3600.0, 0.0) for m in memories],
            dtype=float,
        )
        importance = np.array([float(_attr(m, "importance_score", 0.5)) for m in memories], dtype=float)
        n_access = np.array([int(_attr(m, "retrieval_count", 0) or 0) for m in memories], dtype=float)

        strength = self.s_init * (1.0 + self.gamma * np.log1p(np.maximum(n_access, 0))) * importance
        with np.errstate(divide="ignore", invalid="ignore"):
            retention = np.where(strength > 0, np.exp(-deltas / strength), 0.0)
        return [float(r) for r in retention]
