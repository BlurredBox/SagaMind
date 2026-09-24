"""
SagaMind Episodic Memory Store (TimescaleDB + pgvector)
=======================================================

Relational + vector store for episodic memory traces. Uses a connection pool and
pgvector cosine search when TimescaleDB is reachable, and a deterministic in-memory
emulator otherwise so that local development and the test-suite need no database.

Notes
-----
* The previous implementation referenced ``psycopg2.pool`` without importing the
  submodule, so the pool constructor always raised and was silently swallowed — the
  store therefore *never* used the database. Both the import and the exception handling
  are fixed here.
* Embeddings are bound through the pgvector adapter when available, otherwise as an
  explicit ``'[...]'`` vector literal, so inserts and ``<=>`` cosine queries work.
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from typing import Any

from src.config import settings

logger = logging.getLogger("SagaMind.Memory.Timescale")


def _vector_literal(embedding: list[float]) -> str:
    """Render a Python vector as a pgvector text literal: ``[1,2,3]``."""
    return "[" + ",".join(repr(float(x)) for x in embedding) + "]"


class TimescaleMemoryStore:
    """Episodic memory manager interfacing with TimescaleDB + pgvector."""

    def __init__(self) -> None:
        self.pool: Any = None
        self.pool_active = False
        self._vector_registered = False
        self.fallback_storage: list[dict[str, Any]] = []

        try:
            from psycopg2 import pool as pg_pool

            self.pool = pg_pool.SimpleConnectionPool(
                1,
                10,
                host=settings.db_host,
                port=settings.db_port,
                dbname=settings.db_name,
                user=settings.db_user,
                password=settings.db_pass,
            )
            self._register_vector_adapter()
            self.pool_active = True
            self.initialize_schema()
            logger.info("TimescaleDB connection pool successfully initialized.")
        except ImportError as exc:
            self._handle_unavailable("psycopg2 driver not installed", exc)
        except Exception as exc:  # noqa: BLE001 - DB drivers raise many connection errors
            self._handle_unavailable("TimescaleDB connection failed", exc)

    # ── Availability handling ───────────────────────────────────────────
    def _handle_unavailable(self, reason: str, exc: Exception) -> None:
        if settings.require_backends:
            raise RuntimeError(f"REQUIRE_BACKENDS is set but TimescaleDB is unavailable: {reason}: {exc}") from exc
        logger.warning("%s. Using in-memory episodic store. (%s)", reason, exc)

    def _register_vector_adapter(self) -> None:
        """Register the pgvector type adapter so list embeddings bind correctly."""
        try:
            from pgvector.psycopg2 import register_vector

            conn = self.pool.getconn()
            try:
                register_vector(conn)
                self._vector_registered = True
            finally:
                self.pool.putconn(conn)
        except Exception as exc:  # noqa: BLE001 - adapter is optional, fall back to literals
            logger.debug("pgvector adapter unavailable, using text literals: %s", exc)

    @staticmethod
    def _set_ef_search(cursor: Any) -> None:
        """Set the per-session HNSW query-time search width (§4.6, ``HNSW_EF_SEARCH``)."""
        cursor.execute(f"SET hnsw.ef_search = {settings.hnsw_ef_search};")

    def _bind_embedding(self, embedding: list[float]) -> Any:
        return embedding if self._vector_registered else _vector_literal(embedding)

    # ── Schema ──────────────────────────────────────────────────────────
    def initialize_schema(self) -> None:
        """Create the extension, table, hypertable and vector index if absent."""
        conn = self.pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS episodic_memories (
                        memory_id UUID PRIMARY KEY,
                        tenant_id VARCHAR(50) NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        last_retrieved_at TIMESTAMPTZ NOT NULL,
                        agent_role VARCHAR(50) NOT NULL,
                        summary TEXT NOT NULL,
                        context_data JSONB,
                        importance_score DOUBLE PRECISION NOT NULL,
                        retrieval_count INT NOT NULL DEFAULT 0,
                        embedding VECTOR({settings.embedding_dim})
                    );
                    """
                )
                cursor.execute("CREATE INDEX IF NOT EXISTS idx_episodic_tenant ON episodic_memories (tenant_id);")
                # Approximate-nearest-neighbour index for cosine similarity search.
                # m / ef_construction are tunable via HNSW_M / HNSW_EF_CONSTRUCTION (§4.6) —
                # defaults match pgvector's own defaults; raise for higher recall on larger tenants.
                cursor.execute(
                    f"""
                    CREATE INDEX IF NOT EXISTS idx_episodic_embedding
                    ON episodic_memories USING hnsw (embedding vector_cosine_ops)
                    WITH (m = {settings.hnsw_m}, ef_construction = {settings.hnsw_ef_construction});
                    """
                )
                conn.commit()
            logger.info("TimescaleDB schema checked and initialized.")
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            logger.error("Error during schema initialization: %s", exc)
        finally:
            self.pool.putconn(conn)

    # ── Writes ──────────────────────────────────────────────────────────
    def write_episodic_memory(
        self,
        memory_id: str,
        tenant_id: str,
        agent_role: str,
        summary: str,
        importance: float,
        embedding: list[float],
        context: dict[str, Any] | None = None,
    ) -> None:
        """Insert a new episodic memory node."""
        now = datetime.now(timezone.utc)

        if not self.pool_active:
            self.fallback_storage.append(
                {
                    "memory_id": memory_id,
                    "tenant_id": tenant_id,
                    "created_at": now,
                    "last_retrieved_at": now,
                    "agent_role": agent_role,
                    "summary": summary,
                    "importance_score": importance,
                    "retrieval_count": 0,
                    "embedding": embedding,
                    "context_data": context,
                }
            )
            logger.info("[In-Memory Store] Added episodic memory: '%s'", summary)
            return

        conn = self.pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO episodic_memories (
                        memory_id, tenant_id, created_at, last_retrieved_at,
                        agent_role, summary, context_data, importance_score,
                        retrieval_count, embedding
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """,
                    (
                        memory_id,
                        tenant_id,
                        now,
                        now,
                        agent_role,
                        summary,
                        json.dumps(context or {}),
                        importance,
                        0,
                        self._bind_embedding(embedding),
                    ),
                )
                conn.commit()
            logger.info("[TimescaleDB] Added episodic memory: '%s'", summary)
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            logger.error("Failed to write memory to TimescaleDB: %s", exc)
            if settings.require_backends:
                raise RuntimeError("TimescaleDB memory write failed") from exc
        finally:
            self.pool.putconn(conn)

    # ── Reads ───────────────────────────────────────────────────────────
    def retrieve_similar_memories(
        self,
        tenant_id: str,
        query_embedding: list[float],
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return cosine-similar memories for a tenant with pagination support."""
        if not self.pool_active:
            return self._fallback_similarity(tenant_id, query_embedding, limit, offset)

        conn = self.pool.getconn()
        memories: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cursor:
                self._set_ef_search(cursor)
                cursor.execute(
                    """
                    SELECT memory_id, created_at, last_retrieved_at, agent_role,
                           summary, importance_score, retrieval_count, embedding, context_data
                    FROM episodic_memories
                    WHERE tenant_id = %s
                    ORDER BY embedding <=> %s
                    LIMIT %s OFFSET %s;
                    """,
                    (tenant_id, self._bind_embedding(query_embedding), limit, offset),
                )
                memories = [self._row_to_dict(r) for r in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to query vector similarity in TimescaleDB: %s", exc)
        finally:
            self.pool.putconn(conn)
        return memories

    def mark_retrieved(self, memory_ids: list[str]) -> None:
        """Atomically reinforce retrieved memories and refresh their access time."""
        if not memory_ids:
            return
        now = datetime.now(timezone.utc)
        if not self.pool_active:
            wanted = set(memory_ids)
            for item in self.fallback_storage:
                if str(item["memory_id"]) in wanted:
                    item["retrieval_count"] += 1
                    item["last_retrieved_at"] = now
            return

        conn = self.pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE episodic_memories
                    SET retrieval_count = retrieval_count + 1,
                        last_retrieved_at = now()
                    WHERE memory_id = ANY(%s::uuid[]);
                    """,
                    (memory_ids,),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            conn.rollback()
            logger.error("Failed to reinforce retrieved memories: %s", exc)
            if settings.require_backends:
                raise RuntimeError("TimescaleDB retrieval reinforcement failed") from exc
        finally:
            self.pool.putconn(conn)

    def list_tenants(self) -> list[str]:
        """Return tenants with stored memories for scheduled consolidation."""
        if not self.pool_active:
            return sorted({str(item["tenant_id"]) for item in self.fallback_storage})
        conn = self.pool.getconn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT DISTINCT tenant_id FROM episodic_memories ORDER BY tenant_id;")
                return [str(row[0]) for row in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to enumerate memory tenants: %s", exc)
            if settings.require_backends:
                raise RuntimeError("TimescaleDB tenant enumeration failed") from exc
            return []
        finally:
            self.pool.putconn(conn)

    def retrieve_active_memories(
        self,
        tenant_id: str,
        query_embedding: list[float],
        s_init: float,
        gamma: float,
        tau: float,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return cosine-similar memories whose Ebbinghaus retention is >= tau, computed in SQL.

        Mirrors ``EbbinghausMemoryManager.calculate_retention``:
            S = s_init * (1 + gamma * ln(retrieval_count + 1)) * importance_score
            R = exp(-hours_since_last_retrieved / S)
        """
        if not self.pool_active:
            return self._fallback_active(tenant_id, query_embedding, s_init, gamma, tau, limit, offset)

        conn = self.pool.getconn()
        memories: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cursor:
                self._set_ef_search(cursor)
                cursor.execute(
                    """
                    SELECT memory_id, created_at, last_retrieved_at, agent_role,
                           summary, importance_score, retrieval_count, embedding, context_data,
                           EXP(
                               -EXTRACT(EPOCH FROM (now() - last_retrieved_at)) / 3600.0
                               / GREATEST(%s * (1 + %s * LN(retrieval_count + 1)) * importance_score, 1e-9)
                           ) AS retention
                    FROM episodic_memories
                    WHERE tenant_id = %s
                    AND EXP(
                            -EXTRACT(EPOCH FROM (now() - last_retrieved_at)) / 3600.0
                            / GREATEST(%s * (1 + %s * LN(retrieval_count + 1)) * importance_score, 1e-9)
                        ) >= %s
                    ORDER BY embedding <=> %s
                    LIMIT %s OFFSET %s;
                    """,
                    (
                        s_init,
                        gamma,
                        tenant_id,
                        s_init,
                        gamma,
                        tau,
                        self._bind_embedding(query_embedding),
                        limit,
                        offset,
                    ),
                )
                for r in cursor.fetchall():
                    memory = self._row_to_dict(r[:9])
                    memory["retention"] = float(r[9])
                    memories.append(memory)
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to query active memories in TimescaleDB: %s", exc)
        finally:
            self.pool.putconn(conn)
        return memories

    def _fallback_active(
        self,
        tenant_id: str,
        query_embedding: list[float],
        s_init: float,
        gamma: float,
        tau: float,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        now = datetime.now(timezone.utc)
        out: list[tuple[dict[str, Any], float, float]] = []
        for item in self.fallback_storage:
            if item["tenant_id"] != tenant_id:
                continue
            strength = s_init * (1 + gamma * math.log(item["retrieval_count"] + 1)) * item["importance_score"]
            strength = max(strength, 1e-9)
            last = item["last_retrieved_at"]
            hours = (now - last).total_seconds() / 3600.0
            retention = math.exp(-hours / strength)
            if retention >= tau:
                sim = _cosine_similarity(item["embedding"], query_embedding)
                out.append((item, sim, retention))
        out.sort(key=lambda x: x[1], reverse=True)
        results = []
        for item, _sim, retention in out[offset : offset + limit]:
            m = dict(item)
            m["retention"] = retention
            results.append(m)
        return results

    def get_all_memories(self, tenant_id: str) -> list[dict[str, Any]]:
        """Retrieve every episodic memory for a tenant (used during consolidation)."""
        if not self.pool_active:
            return [m for m in self.fallback_storage if m["tenant_id"] == tenant_id]

        conn = self.pool.getconn()
        memories: list[dict[str, Any]] = []
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT memory_id, created_at, last_retrieved_at, agent_role,
                           summary, importance_score, retrieval_count, embedding, context_data
                    FROM episodic_memories
                    WHERE tenant_id = %s;
                    """,
                    (tenant_id,),
                )
                memories = [self._row_to_dict(r) for r in cursor.fetchall()]
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to retrieve all memories from TimescaleDB: %s", exc)
        finally:
            self.pool.putconn(conn)
        return memories

    # ── Helpers ─────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_dict(r: tuple[Any, ...]) -> dict[str, Any]:
        return {
            "memory_id": str(r[0]),
            "created_at": r[1],
            "last_retrieved_at": r[2],
            "agent_role": r[3],
            "summary": r[4],
            "importance_score": r[5],
            "retrieval_count": r[6],
            "embedding": list(r[7]) if r[7] is not None else [],
            "context_data": r[8],
        }

    def _fallback_similarity(
        self, tenant_id: str, query_embedding: list[float], limit: int, offset: int = 0
    ) -> list[dict[str, Any]]:
        scored: list[tuple[dict[str, Any], float]] = [
            (item, _cosine_similarity(item["embedding"], query_embedding))
            for item in self.fallback_storage
            if item["tenant_id"] == tenant_id
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in scored[offset : offset + limit]]


def _cosine_similarity(u: list[float], v: list[float]) -> float:
    dot = sum(a * b for a, b in zip(u, v, strict=False))
    norm_u = math.sqrt(sum(a * a for a in u))
    norm_v = math.sqrt(sum(b * b for b in v))
    if norm_u == 0 or norm_v == 0:
        return 0.0
    return dot / (norm_u * norm_v)
