"""
SagaMind Durable Saga State Store
=================================

Persists saga lifecycle state and the compensation log so in-flight transactions survive a
process restart and can be rolled back on recovery.

Backends, selected automatically in order of preference:

1. **PostgreSQL / TimescaleDB** — ``saga_transactions`` + ``saga_compensations`` +
   ``saga_dead_letters`` + ``saga_step_idempotency`` tables.  Uses a
   ``ThreadedConnectionPool`` (min 2, max 10) so concurrent API workers do not race over a
   single connection.  Reconnects automatically when the pooled connection is stale.
2. **Redis** — fast shared state when Postgres is unavailable.
3. **In-memory** — development / test fallback (no durability).

Interfaces consumed by the coordinator:

* ``write_transaction_state(saga_id, status, metadata)``
* ``prepare_effect(...)`` — write-ahead registration of an action and compensation
* ``transition_effect(...)`` — atomic compare-and-set lifecycle transition
* ``commit_effect(...)`` — atomic APPLIED→COMMITTED + idempotency publication
* ``get_effect(saga_id, step_id)`` / ``get_effects(saga_id)``
* ``append_compensation(saga_id, tool_name, arguments)``
* ``list_incomplete() -> list[dict]``
* ``step_already_committed(saga_id, idempotency_key) -> bool``
* ``mark_step_committed(saga_id, idempotency_key)``
* ``push_dead_letter(saga_id, step_name, error)``
* ``list_dead_letters() -> list[dict]``
* ``close()``
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.config import settings
from src.models import SagaStatus

logger = logging.getLogger("SagaMind.Orchestrator.StateStore")

_TERMINAL = {
    SagaStatus.COMMITTED.value,
    SagaStatus.ROLLED_BACK.value,
    SagaStatus.COMPENSATION_FAILED.value,
    SagaStatus.FAILED.value,
}


class EffectState(str, Enum):
    """Durable lifecycle for one externally-visible saga effect.

    ``EXECUTING`` and ``COMPENSATING`` are deliberately treated as ambiguous after a
    process restart: the external call may have completed even though its following
    journal write did not.  Recovery must resolve that ambiguity; it must never guess.
    """

    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    APPLIED = "APPLIED"
    COMMITTED = "COMMITTED"
    ABORTED = "ABORTED"
    AMBIGUOUS = "AMBIGUOUS"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"
    COMPENSATION_FAILED = "COMPENSATION_FAILED"


_EFFECT_TERMINAL = {
    EffectState.ABORTED.value,
    EffectState.COMPENSATED.value,
    EffectState.COMPENSATION_FAILED.value,
}


class JournalConflictError(RuntimeError):
    """Raised when an effect journal compare-and-set transition loses a race."""


class SagaStateStore:
    """Durable saga state + compensation log with graceful backend degradation."""

    def __init__(self) -> None:
        self.backend = "memory"
        self._pg_pool: Any = None
        self._redis: Any = None
        # In-memory store — only written when backend == "memory".
        self._state: dict[str, dict[str, Any]] = {}
        self._comps: dict[str, list[dict[str, Any]]] = {}
        self._idem: dict[str, set[str]] = {}  # saga_id → committed idempotency keys
        self._dead: list[dict[str, Any]] = []
        self._history: dict[str, list[dict[str, Any]]] = {}
        self._effects: dict[str, list[dict[str, Any]]] = {}
        self._journal_lock = threading.RLock()

        forced = settings.state_store_backend.lower()
        if forced == "memory":
            pass
        elif forced == "postgres":
            if not self._try_postgres():
                raise RuntimeError("STATE_STORE_BACKEND=postgres but Postgres is unavailable.")
            self.backend = "postgres"
        elif forced == "redis":
            if not self._try_redis():
                raise RuntimeError("STATE_STORE_BACKEND=redis but Redis is unavailable.")
            self.backend = "redis"
        elif self._try_postgres():
            self.backend = "postgres"
        elif self._try_redis():
            self.backend = "redis"
        elif settings.require_backends:
            raise RuntimeError("REQUIRE_BACKENDS is set but no saga state backend is available.")
        logger.info("Saga state store backend: %s", self.backend)

    # ── Backend probes ──────────────────────────────────────────────────
    def _try_postgres(self) -> bool:
        try:
            from psycopg2 import pool as pg_pool

            self._pg_pool = pg_pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=10,
                host=settings.db_host,
                port=settings.db_port,
                dbname=settings.db_name,
                user=settings.db_user,
                password=settings.db_pass,
            )
            self._ensure_pg_schema()
            return True
        except Exception as exc:  # noqa: BLE001 - optional backend
            logger.debug("Postgres saga store unavailable: %s", exc)
            self._pg_pool = None
            return False

    def _try_redis(self) -> bool:
        try:
            import redis

            self._redis = redis.Redis(host=settings.redis_host, port=settings.redis_port, decode_responses=True)
            self._redis.ping()
            return True
        except Exception as exc:  # noqa: BLE001 - optional backend
            logger.debug("Redis saga store unavailable: %s", exc)
            self._redis = None
            return False

    def _pg_conn(self) -> Any:
        """Checkout a connection from the pool, reconnecting the pool if stale."""
        try:
            return self._pg_pool.getconn()
        except Exception:  # noqa: BLE001 - pool closed / all connections broken
            self._try_postgres()
            return self._pg_pool.getconn()

    def _pg_return(self, conn: Any) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            self._pg_pool.putconn(conn)

    def _ensure_pg_schema(self) -> None:
        conn = self._pg_conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS saga_transactions (
                        saga_id    UUID PRIMARY KEY,
                        tenant_id  VARCHAR(50),
                        goal       TEXT,
                        status     VARCHAR(32) NOT NULL,
                        metadata   JSONB,
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    CREATE TABLE IF NOT EXISTS saga_compensations (
                        id        BIGSERIAL PRIMARY KEY,
                        saga_id   UUID NOT NULL,
                        seq       INT NOT NULL,
                        tool_name VARCHAR(64) NOT NULL,
                        arguments JSONB NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS saga_step_idempotency (
                        saga_id         UUID NOT NULL,
                        idempotency_key VARCHAR(128) NOT NULL,
                        committed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                        PRIMARY KEY (saga_id, idempotency_key)
                    );
                    CREATE TABLE IF NOT EXISTS saga_dead_letters (
                        id          BIGSERIAL PRIMARY KEY,
                        saga_id     UUID NOT NULL,
                        tenant_id   VARCHAR(50),
                        step_name   TEXT,
                        error       TEXT,
                        occurred_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    CREATE TABLE IF NOT EXISTS saga_step_history (
                        id         BIGSERIAL PRIMARY KEY,
                        saga_id    UUID NOT NULL,
                        step_name  TEXT,
                        tool_name  TEXT,
                        arguments  JSONB,
                        result     JSONB,
                        status     VARCHAR(32),
                        recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
                    );
                    CREATE TABLE IF NOT EXISTS saga_effect_journal (
                        id                     BIGSERIAL PRIMARY KEY,
                        saga_id                UUID NOT NULL,
                        seq                    INT NOT NULL,
                        step_id                TEXT NOT NULL,
                        step_name              TEXT NOT NULL,
                        action_tool_name       TEXT NOT NULL,
                        action_arguments       JSONB NOT NULL,
                        compensation_tool_name TEXT NOT NULL,
                        compensation_arguments JSONB NOT NULL,
                        idempotency_key        VARCHAR(128),
                        state                  VARCHAR(32) NOT NULL,
                        result                 JSONB,
                        error                  TEXT,
                        created_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
                        updated_at             TIMESTAMPTZ NOT NULL DEFAULT now(),
                        UNIQUE (saga_id, step_id),
                        UNIQUE (saga_id, seq)
                    );
                    CREATE INDEX IF NOT EXISTS idx_saga_effect_recovery
                        ON saga_effect_journal (saga_id, state, seq);
                    ALTER TABLE saga_dead_letters ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50);
                    """
                )
        finally:
            self._pg_return(conn)

    # ── Writes ──────────────────────────────────────────────────────────
    def write_transaction_state(self, saga_id: str, status: str, metadata: dict[str, Any]) -> None:
        """Upsert saga status and metadata."""
        if self.backend == "postgres":
            self._pg_write_state(saga_id, status, metadata)
        elif self.backend == "redis":
            existing = json.loads(self._redis.hget(f"saga:{saga_id}", "metadata") or "{}")
            existing.update(metadata or {})
            self._redis.hset(
                f"saga:{saga_id}",
                mapping={"status": status, "metadata": json.dumps(existing)},
            )
            if status in _TERMINAL:
                self._redis.srem("sagas:incomplete", saga_id)
            else:
                self._redis.sadd("sagas:incomplete", saga_id)
        else:
            rec = self._state.setdefault(saga_id, {"saga_id": saga_id, "metadata": {}})
            rec["status"] = status
            rec["metadata"].update(metadata or {})

    def append_compensation(self, saga_id: str, tool_name: str, arguments: dict[str, Any]) -> None:
        """Persist a committed step's compensation for crash-recovery replay."""
        if self.backend == "postgres":
            conn = self._pg_conn()
            try:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COALESCE(MAX(seq), 0) + 1 FROM saga_compensations WHERE saga_id = %s;",
                        (saga_id,),
                    )
                    seq = cur.fetchone()[0]
                    cur.execute(
                        "INSERT INTO saga_compensations (saga_id, seq, tool_name, arguments) VALUES (%s, %s, %s, %s);",
                        (saga_id, seq, tool_name, json.dumps(arguments)),
                    )
            finally:
                self._pg_return(conn)
        elif self.backend == "redis":
            self._redis.rpush(
                f"saga:{saga_id}:comps",
                json.dumps({"tool_name": tool_name, "arguments": arguments}),
            )
        else:
            self._comps.setdefault(saga_id, []).append({"tool_name": tool_name, "arguments": arguments})

    def _pg_write_state(self, saga_id: str, status: str, metadata: dict[str, Any]) -> None:
        conn = self._pg_conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO saga_transactions
                        (saga_id, tenant_id, goal, status, metadata, updated_at)
                    VALUES (%s, %s, %s, %s, %s, now())
                    ON CONFLICT (saga_id) DO UPDATE SET
                        status     = EXCLUDED.status,
                        metadata   = COALESCE(saga_transactions.metadata, '{}'::jsonb)
                                     || EXCLUDED.metadata,
                        tenant_id  = COALESCE(EXCLUDED.tenant_id, saga_transactions.tenant_id),
                        goal       = COALESCE(EXCLUDED.goal, saga_transactions.goal),
                        updated_at = now();
                    """,
                    (
                        saga_id,
                        metadata.get("tenant_id"),
                        metadata.get("goal"),
                        status,
                        json.dumps(metadata or {}),
                    ),
                )
        finally:
            self._pg_return(conn)

    # ── Crash-consistent effect journal ────────────────────────────────
    def prepare_effect(
        self,
        saga_id: str,
        step_id: str,
        step_name: str,
        action_tool_name: str,
        action_arguments: dict[str, Any],
        compensation_tool_name: str,
        compensation_arguments: dict[str, Any],
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Durably register an effect and its inverse *before* external execution.

        The operation is idempotent for an identical ``(saga_id, step_id)``.  Reusing
        the identity for different payloads is rejected because silently changing a
        prepared compensation would make recovery unsafe.
        """
        payload: dict[str, Any] = {
            "saga_id": saga_id,
            "step_id": step_id,
            "step_name": step_name,
            "action": {"tool_name": action_tool_name, "arguments": action_arguments},
            "compensation": {
                "tool_name": compensation_tool_name,
                "arguments": compensation_arguments,
            },
            "idempotency_key": idempotency_key,
            "state": EffectState.PREPARED.value,
            "result": None,
            "error": None,
        }
        if self.backend == "postgres":
            return self._pg_prepare_effect(payload)
        if self.backend == "redis":
            return self._redis_prepare_effect(payload)
        with self._journal_lock:
            existing = next((e for e in self._effects.get(saga_id, []) if e["step_id"] == step_id), None)
            if existing is not None:
                self._assert_same_effect(existing, payload)
                return dict(existing)
            entry = dict(payload)
            entry["seq"] = len(self._effects.get(saga_id, [])) + 1
            entry["created_at"] = self._now()
            entry["updated_at"] = entry["created_at"]
            self._effects.setdefault(saga_id, []).append(entry)
            return dict(entry)

    def transition_effect(
        self,
        saga_id: str,
        step_id: str,
        expected_states: str | set[str] | tuple[str, ...] | list[str],
        new_state: str,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Atomically compare-and-set one journal record.

        Strict transitions make duplicate workers and stale recovery attempts fail
        closed instead of executing the same external effect twice.
        """
        expected = {expected_states} if isinstance(expected_states, str) else set(expected_states)
        if self.backend == "postgres":
            return self._pg_transition_effect(saga_id, step_id, expected, new_state, result, error)
        if self.backend == "redis":
            return self._redis_transition_effect(saga_id, step_id, expected, new_state, result, error)
        with self._journal_lock:
            entry = self._memory_effect(saga_id, step_id)
            if entry["state"] not in expected:
                raise JournalConflictError(
                    f"Effect {saga_id}/{step_id} is {entry['state']}; expected {sorted(expected)}"
                )
            entry["state"] = new_state
            entry["result"] = result
            entry["error"] = error
            entry["updated_at"] = self._now()
            return dict(entry)

    def commit_effect(
        self,
        saga_id: str,
        step_id: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Atomically mark an applied effect committed and publish its dedupe key."""
        if self.backend == "postgres":
            conn = self._pg_conn()
            try:
                conn.autocommit = False
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE saga_effect_journal SET state = %s, updated_at = now() "
                        "WHERE saga_id = %s AND step_id = %s AND state = %s RETURNING "
                        "seq, step_name, action_tool_name, action_arguments, "
                        "compensation_tool_name, compensation_arguments, idempotency_key, "
                        "state, result, error, created_at, updated_at;",
                        (
                            EffectState.COMMITTED.value,
                            saga_id,
                            step_id,
                            EffectState.APPLIED.value,
                        ),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise JournalConflictError(f"Effect {saga_id}/{step_id} is not APPLIED")
                    if idempotency_key:
                        cur.execute(
                            "INSERT INTO saga_step_idempotency (saga_id, idempotency_key) "
                            "VALUES (%s, %s) ON CONFLICT DO NOTHING;",
                            (saga_id, idempotency_key),
                        )
                conn.commit()
                return self._effect_from_pg_row(saga_id, step_id, row)
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.autocommit = True
                self._pg_return(conn)
        if self.backend == "redis":
            key = self._redis_effect_key(saga_id, step_id)
            script = """
            if redis.call('HGET', KEYS[1], 'state') ~= ARGV[1] then return 0 end
            redis.call('HSET', KEYS[1], 'state', ARGV[2], 'updated_at', ARGV[3])
            if ARGV[4] ~= '' then redis.call('SADD', KEYS[2], ARGV[4]) end
            return 1
            """
            ok = self._redis.eval(
                script,
                2,
                key,
                f"saga:{saga_id}:idem",
                EffectState.APPLIED.value,
                EffectState.COMMITTED.value,
                self._now(),
                idempotency_key or "",
            )
            if not ok:
                raise JournalConflictError(f"Effect {saga_id}/{step_id} is not APPLIED")
            return self.get_effect(saga_id, step_id)
        with self._journal_lock:
            entry = self._memory_effect(saga_id, step_id)
            if entry["state"] != EffectState.APPLIED.value:
                raise JournalConflictError(f"Effect {saga_id}/{step_id} is not APPLIED")
            entry["state"] = EffectState.COMMITTED.value
            entry["updated_at"] = self._now()
            if idempotency_key:
                self._idem.setdefault(saga_id, set()).add(idempotency_key)
            return dict(entry)

    def get_effect(self, saga_id: str, step_id: str) -> dict[str, Any]:
        effects = self.get_effects(saga_id)
        match = next((e for e in effects if e["step_id"] == step_id), None)
        if match is None:
            raise KeyError(f"Unknown effect {saga_id}/{step_id}")
        return match

    def get_effects(self, saga_id: str) -> list[dict[str, Any]]:
        """Return journal records in forward execution order."""
        if self.backend == "postgres":
            conn = self._pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT step_id, seq, step_name, action_tool_name, action_arguments, "
                        "compensation_tool_name, compensation_arguments, idempotency_key, "
                        "state, result, error, created_at, updated_at "
                        "FROM saga_effect_journal WHERE saga_id = %s ORDER BY seq;",
                        (saga_id,),
                    )
                    return [self._effect_from_pg_select_row(saga_id, row) for row in cur.fetchall()]
            finally:
                self._pg_return(conn)
        if self.backend == "redis":
            step_ids = self._redis.lrange(f"saga:{saga_id}:effects", 0, -1)
            return [
                self._redis_decode_effect(self._redis.hgetall(self._redis_effect_key(saga_id, sid))) for sid in step_ids
            ]
        with self._journal_lock:
            return [dict(entry) for entry in self._effects.get(saga_id, [])]

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _assert_same_effect(existing: dict[str, Any], proposed: dict[str, Any]) -> None:
        fields = ("step_name", "action", "compensation", "idempotency_key")
        if any(existing.get(field) != proposed.get(field) for field in fields):
            raise JournalConflictError(
                f"Step identity {proposed['saga_id']}/{proposed['step_id']} was reused with a different effect"
            )

    def _memory_effect(self, saga_id: str, step_id: str) -> dict[str, Any]:
        entry = next((e for e in self._effects.get(saga_id, []) if e["step_id"] == step_id), None)
        if entry is None:
            raise KeyError(f"Unknown effect {saga_id}/{step_id}")
        return entry

    def _pg_prepare_effect(self, payload: dict[str, Any]) -> dict[str, Any]:
        conn = self._pg_conn()
        try:
            conn.autocommit = False
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s));", (payload["saga_id"],))
                cur.execute(
                    "SELECT step_id, seq, step_name, action_tool_name, action_arguments, "
                    "compensation_tool_name, compensation_arguments, idempotency_key, state, "
                    "result, error, created_at, updated_at FROM saga_effect_journal "
                    "WHERE saga_id = %s AND step_id = %s;",
                    (payload["saga_id"], payload["step_id"]),
                )
                row = cur.fetchone()
                if row is not None:
                    existing = self._effect_from_pg_select_row(payload["saga_id"], row)
                    self._assert_same_effect(existing, payload)
                    conn.commit()
                    return existing
                cur.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM saga_effect_journal WHERE saga_id = %s;",
                    (payload["saga_id"],),
                )
                seq = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO saga_effect_journal "
                    "(saga_id, seq, step_id, step_name, action_tool_name, action_arguments, "
                    "compensation_tool_name, compensation_arguments, idempotency_key, state) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s);",
                    (
                        payload["saga_id"],
                        seq,
                        payload["step_id"],
                        payload["step_name"],
                        payload["action"]["tool_name"],
                        json.dumps(payload["action"]["arguments"]),
                        payload["compensation"]["tool_name"],
                        json.dumps(payload["compensation"]["arguments"]),
                        payload["idempotency_key"],
                        EffectState.PREPARED.value,
                    ),
                )
            conn.commit()
            return self.get_effect(payload["saga_id"], payload["step_id"])
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.autocommit = True
            self._pg_return(conn)

    def _pg_transition_effect(
        self,
        saga_id: str,
        step_id: str,
        expected: set[str],
        new_state: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> dict[str, Any]:
        conn = self._pg_conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE saga_effect_journal SET state = %s, result = %s, error = %s, updated_at = now() "
                    "WHERE saga_id = %s AND step_id = %s AND state = ANY(%s) RETURNING "
                    "seq, step_name, action_tool_name, action_arguments, compensation_tool_name, "
                    "compensation_arguments, idempotency_key, state, result, error, created_at, updated_at;",
                    (
                        new_state,
                        json.dumps(result) if result is not None else None,
                        error,
                        saga_id,
                        step_id,
                        list(expected),
                    ),
                )
                row = cur.fetchone()
                if row is None:
                    raise JournalConflictError(
                        f"Effect {saga_id}/{step_id} did not match expected state {sorted(expected)}"
                    )
                return self._effect_from_pg_row(saga_id, step_id, row)
        finally:
            self._pg_return(conn)

    def _effect_from_pg_row(self, saga_id: str, step_id: str, row: Any) -> dict[str, Any]:
        return {
            "saga_id": saga_id,
            "step_id": step_id,
            "seq": row[0],
            "step_name": row[1],
            "action": {"tool_name": row[2], "arguments": row[3]},
            "compensation": {"tool_name": row[4], "arguments": row[5]},
            "idempotency_key": row[6],
            "state": row[7],
            "result": row[8],
            "error": row[9],
            "created_at": row[10].isoformat() if row[10] else None,
            "updated_at": row[11].isoformat() if row[11] else None,
        }

    def _effect_from_pg_select_row(self, saga_id: str, row: Any) -> dict[str, Any]:
        return self._effect_from_pg_row(saga_id, row[0], row[1:])

    @staticmethod
    def _redis_effect_key(saga_id: str, step_id: str) -> str:
        return f"saga:{saga_id}:effect:{step_id}"

    def _redis_prepare_effect(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = self._redis_effect_key(payload["saga_id"], payload["step_id"])
        created = self._now()
        seq = int(self._redis.llen(f"saga:{payload['saga_id']}:effects")) + 1
        fields = {
            "saga_id": payload["saga_id"],
            "step_id": payload["step_id"],
            "step_name": payload["step_name"],
            "action": json.dumps(payload["action"]),
            "compensation": json.dumps(payload["compensation"]),
            "idempotency_key": payload["idempotency_key"] or "",
            "state": EffectState.PREPARED.value,
            "result": "null",
            "error": "",
            "seq": str(seq),
            "created_at": created,
            "updated_at": created,
        }
        script = """
        if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
        for i = 1, #ARGV, 2 do redis.call('HSET', KEYS[1], ARGV[i], ARGV[i + 1]) end
        redis.call('RPUSH', KEYS[2], ARGV[4])
        return 1
        """
        flat = [item for pair in fields.items() for item in pair]
        inserted = self._redis.eval(script, 2, key, f"saga:{payload['saga_id']}:effects", *flat)
        existing = self._redis_decode_effect(self._redis.hgetall(key))
        if not inserted:
            self._assert_same_effect(existing, payload)
        return existing

    def _redis_transition_effect(
        self,
        saga_id: str,
        step_id: str,
        expected: set[str],
        new_state: str,
        result: dict[str, Any] | None,
        error: str | None,
    ) -> dict[str, Any]:
        key = self._redis_effect_key(saga_id, step_id)
        # A compact WATCH transaction is clearer and less error-prone than encoding
        # the variable expected-state set into Lua arguments.
        with self._redis.pipeline() as pipe:
            while True:
                try:
                    pipe.watch(key)
                    current = pipe.hget(key, "state")
                    if current not in expected:
                        raise JournalConflictError(
                            f"Effect {saga_id}/{step_id} is {current}; expected {sorted(expected)}"
                        )
                    pipe.multi()
                    pipe.hset(
                        key,
                        mapping={
                            "state": new_state,
                            "result": json.dumps(result),
                            "error": error or "",
                            "updated_at": self._now(),
                        },
                    )
                    pipe.execute()
                    break
                except Exception as exc:
                    if exc.__class__.__name__ == "WatchError":
                        continue
                    raise
        return self.get_effect(saga_id, step_id)

    @staticmethod
    def _redis_decode_effect(raw: dict[str, str]) -> dict[str, Any]:
        if not raw:
            raise KeyError("Unknown Redis effect record")
        return {
            "saga_id": raw["saga_id"],
            "step_id": raw["step_id"],
            "seq": int(raw["seq"]),
            "step_name": raw["step_name"],
            "action": json.loads(raw["action"]),
            "compensation": json.loads(raw["compensation"]),
            "idempotency_key": raw.get("idempotency_key") or None,
            "state": raw["state"],
            "result": json.loads(raw.get("result") or "null"),
            "error": raw.get("error") or None,
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
        }

    # ── Idempotency ─────────────────────────────────────────────────────
    def step_already_committed(self, saga_id: str, idempotency_key: str) -> bool:
        """Return True if this (saga_id, idempotency_key) pair was previously committed."""
        if self.backend == "postgres":
            conn = self._pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT 1 FROM saga_step_idempotency WHERE saga_id = %s AND idempotency_key = %s;",
                        (saga_id, idempotency_key),
                    )
                    return cur.fetchone() is not None
            finally:
                self._pg_return(conn)
        elif self.backend == "redis":
            return bool(self._redis.sismember(f"saga:{saga_id}:idem", idempotency_key))
        else:
            return idempotency_key in self._idem.get(saga_id, set())

    def mark_step_committed(self, saga_id: str, idempotency_key: str) -> None:
        """Record that this step was successfully committed (for deduplication)."""
        if self.backend == "postgres":
            conn = self._pg_conn()
            try:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO saga_step_idempotency (saga_id, idempotency_key) "
                        "VALUES (%s, %s) ON CONFLICT DO NOTHING;",
                        (saga_id, idempotency_key),
                    )
            finally:
                self._pg_return(conn)
        elif self.backend == "redis":
            self._redis.sadd(f"saga:{saga_id}:idem", idempotency_key)
        else:
            self._idem.setdefault(saga_id, set()).add(idempotency_key)

    # ── Dead-letter queue ────────────────────────────────────────────────
    def push_dead_letter(
        self,
        saga_id: str,
        step_name: str,
        error: str,
        tenant_id: str | None = None,
    ) -> None:
        """Record a saga that reached COMPENSATION_FAILED for manual operator review."""
        tenant_id = tenant_id or self._tenant_for_saga(saga_id)
        entry: dict[str, Any] = {
            "saga_id": saga_id,
            "tenant_id": tenant_id,
            "step_name": step_name,
            "error": error,
        }
        if self.backend == "postgres":
            conn = self._pg_conn()
            try:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO saga_dead_letters (saga_id, tenant_id, step_name, error) VALUES (%s, %s, %s, %s);",
                        (saga_id, tenant_id, step_name, error),
                    )
            finally:
                self._pg_return(conn)
        elif self.backend == "redis":
            self._redis.lpush("sagas:dead_letter", json.dumps(entry))
        else:
            self._dead.append(entry)

    def list_dead_letters(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Return all dead-letter entries ordered newest-first."""
        if self.backend == "postgres":
            conn = self._pg_conn()
            try:
                with conn.cursor() as cur:
                    if tenant_id is None:
                        cur.execute(
                            "SELECT saga_id, tenant_id, step_name, error, occurred_at "
                            "FROM saga_dead_letters ORDER BY occurred_at DESC LIMIT 500;"
                        )
                    else:
                        cur.execute(
                            "SELECT saga_id, tenant_id, step_name, error, occurred_at "
                            "FROM saga_dead_letters WHERE tenant_id = %s "
                            "ORDER BY occurred_at DESC LIMIT 500;",
                            (tenant_id,),
                        )
                    return [
                        {
                            "saga_id": str(r[0]),
                            "tenant_id": r[1],
                            "step_name": r[2],
                            "error": r[3],
                            "occurred_at": r[4].isoformat() if r[4] else None,
                        }
                        for r in cur.fetchall()
                    ]
            finally:
                self._pg_return(conn)
        elif self.backend == "redis":
            raw = self._redis.lrange("sagas:dead_letter", 0, 499)
            entries = [json.loads(r) for r in raw]
            return [entry for entry in entries if tenant_id is None or entry.get("tenant_id") == tenant_id]
        else:
            return [entry for entry in reversed(self._dead) if tenant_id is None or entry.get("tenant_id") == tenant_id]

    def _tenant_for_saga(self, saga_id: str) -> str | None:
        if self.backend == "postgres":
            conn = self._pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT tenant_id FROM saga_transactions WHERE saga_id = %s;", (saga_id,))
                    row = cur.fetchone()
                    return str(row[0]) if row and row[0] is not None else None
            finally:
                self._pg_return(conn)
        if self.backend == "redis":
            metadata = json.loads(self._redis.hget(f"saga:{saga_id}", "metadata") or "{}")
            value = metadata.get("tenant_id")
            return str(value) if value is not None else None
        value = self._state.get(saga_id, {}).get("metadata", {}).get("tenant_id")
        return str(value) if value is not None else None

    # ── Replay / history ─────────────────────────────────────────────────
    def record_step(
        self,
        saga_id: str,
        step_name: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any],
        status: str,
    ) -> None:
        """Append a forward-execution record for replay/time-travel debugging."""
        entry = {
            "step_name": step_name,
            "tool_name": tool_name,
            "arguments": arguments,
            "result": result,
            "status": status,
        }
        if self.backend == "postgres":
            conn = self._pg_conn()
            try:
                conn.autocommit = True
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO saga_step_history "
                        "(saga_id, step_name, tool_name, arguments, result, status) "
                        "VALUES (%s, %s, %s, %s, %s, %s);",
                        (saga_id, step_name, tool_name, json.dumps(arguments), json.dumps(result), status),
                    )
            finally:
                self._pg_return(conn)
        elif self.backend == "redis":
            self._redis.rpush(f"saga:{saga_id}:history", json.dumps(entry))
        else:
            self._history.setdefault(saga_id, []).append(entry)

    def get_history(self, saga_id: str) -> list[dict[str, Any]]:
        """Return the ordered forward-execution history for a saga."""
        if self.backend == "postgres":
            conn = self._pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT step_name, tool_name, arguments, result, status, recorded_at "
                        "FROM saga_step_history WHERE saga_id = %s ORDER BY id;",
                        (saga_id,),
                    )
                    return [
                        {
                            "step_name": r[0],
                            "tool_name": r[1],
                            "arguments": r[2],
                            "result": r[3],
                            "status": r[4],
                            "recorded_at": r[5].isoformat() if r[5] else None,
                        }
                        for r in cur.fetchall()
                    ]
            finally:
                self._pg_return(conn)
        elif self.backend == "redis":
            return [json.loads(r) for r in self._redis.lrange(f"saga:{saga_id}:history", 0, -1)]
        else:
            return list(self._history.get(saga_id, []))

    # ── Reads / recovery ────────────────────────────────────────────────
    def list_incomplete(self) -> list[dict[str, Any]]:
        """Return non-terminal sagas with their ordered compensation log."""
        if self.backend == "postgres":
            return self._pg_list_incomplete()
        if self.backend == "redis":
            return self._redis_list_incomplete()
        return [
            {
                "saga_id": sid,
                "status": rec.get("status"),
                "metadata": dict(rec.get("metadata", {})),
                "tenant_id": rec.get("metadata", {}).get("tenant_id"),
                "goal": rec.get("metadata", {}).get("goal"),
                "compensations": list(self._comps.get(sid, [])),
                "effects": [dict(effect) for effect in self._effects.get(sid, [])],
            }
            for sid, rec in self._state.items()
            if rec.get("status") not in _TERMINAL
        ]

    def _pg_list_incomplete(self) -> list[dict[str, Any]]:
        conn = self._pg_conn()
        try:
            out: list[dict[str, Any]] = []
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT saga_id, status, metadata, tenant_id, goal FROM saga_transactions WHERE status NOT IN %s;",
                    (tuple(_TERMINAL),),
                )
                sagas = cur.fetchall()
                for saga_row in sagas:
                    sid = str(saga_row[0])
                    cur.execute(
                        "SELECT tool_name, arguments FROM saga_compensations WHERE saga_id = %s ORDER BY seq;",
                        (sid,),
                    )
                    comps = [{"tool_name": r[0], "arguments": r[1]} for r in cur.fetchall()]
                    out.append(
                        {
                            "saga_id": sid,
                            "status": saga_row[1],
                            "metadata": saga_row[2] or {},
                            "tenant_id": saga_row[3],
                            "goal": saga_row[4],
                            "compensations": comps,
                            "effects": self.get_effects(sid),
                        }
                    )
            return out
        finally:
            self._pg_return(conn)

    def _redis_list_incomplete(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for sid in self._redis.smembers("sagas:incomplete"):
            comps = [json.loads(c) for c in self._redis.lrange(f"saga:{sid}:comps", 0, -1)]
            raw = self._redis.hgetall(f"saga:{sid}")
            metadata = json.loads(raw.get("metadata") or "{}")
            out.append(
                {
                    "saga_id": sid,
                    "status": raw.get("status"),
                    "metadata": metadata,
                    "tenant_id": metadata.get("tenant_id"),
                    "goal": metadata.get("goal"),
                    "compensations": comps,
                    "effects": self.get_effects(sid),
                }
            )
        return out

    def close(self) -> None:
        if self._pg_pool is not None:
            try:
                self._pg_pool.closeall()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Error closing Postgres pool: %s", exc)
