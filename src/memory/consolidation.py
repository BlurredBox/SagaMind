"""
SagaMind Memory Consolidation ("Sleep Cycle")
=============================================

Groups semantically similar episodic memories and distils each group into concept
relationships written to the semantic graph. Clustering uses deterministic DBSCAN over
cosine distance. One implementation is used in every environment, avoiding result drift
based on whether scikit-learn happens to be installed.

If an LLM client is supplied, each cluster is labelled with a distilled concept summary;
otherwise a deterministic ``Cluster {n} Concept`` label is used so the pipeline is fully
functional offline and in tests.
"""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger("SagaMind.Memory.Consolidation")


def _embedding(ep: Any) -> Any:
    return ep.get("embedding") if isinstance(ep, dict) else ep.embedding


def _field(ep: Any, name: str) -> Any:
    return ep.get(name) if isinstance(ep, dict) else getattr(ep, name)


class MemoryConsolidator:
    """Clusters episodic memories and projects them into the semantic graph."""

    def __init__(self, timescale_store: Any, neo4j_store: Any, llm_client: Any | None = None):
        self.db = timescale_store
        self.graph = neo4j_store
        self.llm: Any = llm_client

    # ── Distance ────────────────────────────────────────────────────────
    def compute_cosine_distance(self, u: list[float], v: list[float]) -> float:
        """Cosine distance in [0, 2]; a zero vector yields the maximal in-set distance 1.0."""
        dot = sum(a * b for a, b in zip(u, v, strict=False))
        norm_u = math.sqrt(sum(a * a for a in u))
        norm_v = math.sqrt(sum(b * b for b in v))
        if norm_u == 0 or norm_v == 0:
            return 1.0
        return 1.0 - (dot / (norm_u * norm_v))

    def _distance_matrix(self, embeddings: list[list[float]]) -> Any:
        """Return an (n, n) cosine-distance matrix.

        Tiered implementation (§4.5): native Rust/PyO3 kernel (``sagamind_native``) when
        built and installed > vectorised NumPy > pure-Python double loop. All three are
        numerically equivalent; this is a pure performance optimisation.
        """
        n = len(embeddings)
        try:
            import sagamind_native

            widths = {len(e) for e in embeddings}
            if len(widths) != 1:
                raise ValueError("ragged embeddings")
            return sagamind_native.cosine_distance_matrix(embeddings)
        except Exception:  # noqa: BLE001 - native extension absent or ragged input
            pass
        try:
            import numpy as np

            widths = {len(e) for e in embeddings}
            if len(widths) != 1:  # ragged embeddings → fall back to scalar path
                raise ValueError("ragged embeddings")
            m = np.asarray(embeddings, dtype=float)
            norms = np.linalg.norm(m, axis=1)
            safe = norms.copy()
            safe[safe == 0] = 1.0
            unit = m / safe[:, None]
            sim = unit @ unit.T
            dist = 1.0 - sim
            zero = norms == 0
            if zero.any():  # any zero-norm vector is maximally distant from everything
                dist[zero, :] = 1.0
                dist[:, zero] = 1.0
            return dist
        except Exception:  # noqa: BLE001 - NumPy missing or ragged input
            return [[self.compute_cosine_distance(embeddings[i], embeddings[j]) for j in range(n)] for i in range(n)]

    # ── Cluster labelling ───────────────────────────────────────────────
    def _label_cluster(self, cluster_id: int, cluster: list[Any]) -> str:
        """Distil a cluster into a concept label (LLM-backed when available)."""
        if self.llm is None:
            return f"Cluster {cluster_id} Concept"
        summaries = [str(_field(item, "summary")) for item in cluster]
        try:
            return self._llm_summarize(summaries)
        except Exception as exc:  # noqa: BLE001 - never let the LLM break consolidation
            logger.warning("LLM cluster distillation failed, using fallback label: %s", exc)
            return f"Cluster {cluster_id} Concept"

    def _llm_summarize(self, summaries: list[str], timeout_s: float = 10.0) -> str:
        """Override-point / thin wrapper around the configured LLM client.

        Runs the (potentially blocking) client call on a worker thread so a hung LLM
        cannot stall the consolidation cycle indefinitely.
        """
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as FutureTimeoutError

        joined = "\n- ".join(summaries)
        prompt = f"Summarise the shared concept behind these agent experiences in a short noun phrase:\n- {joined}"
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.llm.summarize, prompt)
            try:
                return future.result(timeout=timeout_s)  # type: ignore[no-any-return]
            except FutureTimeoutError as exc:
                raise TimeoutError(f"LLM summarize call exceeded {timeout_s}s") from exc

    # ── Main cycle ──────────────────────────────────────────────────────
    def run_consolidation_cycle(self, tenant_id: str, eps: float = 0.2) -> int:
        """Cluster a tenant's episodic memories and write concept edges.

        Returns the number of clusters (including singletons) discovered.
        """
        logger.info("[Sleep Cycle] Starting consolidation cycle for tenant: '%s'", tenant_id)
        episodes = self._fetch_episodes(tenant_id)

        if len(episodes) < 2:
            logger.info("Insufficient memories to run sleep consolidation cycle.")
            return 0

        clusters = self._cluster(episodes, eps)

        for cid, cluster in clusters.items():
            if len(cluster) < 2:
                continue  # isolated occurrence treated as noise
            concept = self._label_cluster(cid, cluster)
            for item in cluster:
                self.graph.upsert_relationship(
                    source=concept,
                    relation="SUMMARIZES_EXPERIENCE",
                    target=_field(item, "summary"),
                    weight=0.7,
                )
                self.graph.upsert_relationship(
                    source=_field(item, "agent_role"),
                    relation="DISCOVERED_CONCEPT",
                    target=concept,
                    weight=0.5,
                )

        logger.info("Sleep consolidation cycle complete. %d clusters processed.", len(clusters))
        return len(clusters)

    # ── Internals ───────────────────────────────────────────────────────
    def _fetch_episodes(self, tenant_id: str) -> list[Any]:
        if hasattr(self.db, "get_all_memories"):
            episodes: list[Any] = self.db.get_all_memories(tenant_id)
            return episodes
        if hasattr(self.db, "fallback_storage"):
            return [m for m in self.db.fallback_storage if m["tenant_id"] == tenant_id]
        return []

    def _cluster(self, episodes: list[Any], eps: float, min_samples: int = 2) -> dict[int, list[Any]]:
        """Cluster episodes with deterministic DBSCAN over cosine distance.

        ``min_samples`` includes the point itself, matching the standard DBSCAN
        definition. Noise points are omitted from the returned mapping.
        """
        embeddings = [_embedding(ep) for ep in episodes]
        dist = self._distance_matrix(embeddings)
        n = len(episodes)
        unvisited = -2
        noise = -1
        labels = [unvisited] * n
        cluster_id = 0

        def neighbours(index: int) -> list[int]:
            return [j for j in range(n) if float(dist[index][j]) <= eps]

        for i in range(n):
            if labels[i] != unvisited:
                continue
            near = neighbours(i)
            if len(near) < min_samples:
                labels[i] = noise
                continue
            labels[i] = cluster_id
            seeds = [j for j in near if j != i]
            queued = set(seeds)
            cursor = 0
            while cursor < len(seeds):
                point = seeds[cursor]
                cursor += 1
                if labels[point] == noise:
                    labels[point] = cluster_id
                if labels[point] != unvisited:
                    continue
                labels[point] = cluster_id
                point_near = neighbours(point)
                if len(point_near) >= min_samples:
                    for candidate in point_near:
                        if candidate not in queued and labels[candidate] in {unvisited, noise}:
                            seeds.append(candidate)
                            queued.add(candidate)
            cluster_id += 1

        clusters: dict[int, list[Any]] = {}
        for idx, label in enumerate(labels):
            if label >= 0:
                clusters.setdefault(label, []).append(episodes[idx])
        return clusters
