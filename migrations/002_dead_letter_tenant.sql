ALTER TABLE saga_dead_letters
    ADD COLUMN IF NOT EXISTS tenant_id VARCHAR(50);

CREATE INDEX IF NOT EXISTS idx_saga_dead_letters_tenant
    ON saga_dead_letters (tenant_id, occurred_at DESC);
