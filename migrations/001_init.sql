-- SagaMind initial schema (TimescaleDB + pgvector)
-- ================================================
-- Mounted into the TimescaleDB container's docker-entrypoint-initdb.d so the schema is
-- created on first boot. The application's runtime initialize_schema() is idempotent and
-- mirrors this file; managed migrations (Alembic) are the recommended next step — see
-- improve.md §5.4.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE IF NOT EXISTS episodic_memories (
    memory_id          UUID PRIMARY KEY,
    tenant_id          VARCHAR(50) NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL,
    last_retrieved_at  TIMESTAMPTZ NOT NULL,
    agent_role         VARCHAR(50) NOT NULL,
    summary            TEXT NOT NULL,
    context_data       JSONB,
    importance_score   DOUBLE PRECISION NOT NULL,
    retrieval_count    INT NOT NULL DEFAULT 0,
    embedding          VECTOR(1536)
);

-- Time-series partitioning on encode time for retention policies and fast time scans.
SELECT create_hypertable('episodic_memories', 'created_at', if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS idx_episodic_tenant
    ON episodic_memories (tenant_id);

-- Approximate-nearest-neighbour index for cosine similarity retrieval.
CREATE INDEX IF NOT EXISTS idx_episodic_embedding
    ON episodic_memories USING hnsw (embedding vector_cosine_ops);

-- Durable saga transaction log (crash recovery / audit). The coordinator persists state
-- transitions here when wired with a db_client (see improve.md §3.6).
CREATE TABLE IF NOT EXISTS saga_transactions (
    saga_id      UUID PRIMARY KEY,
    tenant_id    VARCHAR(50) NOT NULL,
    goal         TEXT NOT NULL,
    status       VARCHAR(32) NOT NULL,
    metadata     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_saga_tenant
    ON saga_transactions (tenant_id);

-- Write-ahead effect journal.  The coordinator inserts PREPARED before invoking an
-- external tool, then advances the row with compare-and-set transitions.  EXECUTING
-- and COMPENSATING rows are intentionally recoverable ambiguity markers: a process
-- may have died after the external call but before the next durable write.
CREATE TABLE IF NOT EXISTS saga_effect_journal (
    id                     BIGSERIAL PRIMARY KEY,
    saga_id                UUID NOT NULL REFERENCES saga_transactions(saga_id),
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
