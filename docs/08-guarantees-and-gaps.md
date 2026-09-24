# Guarantees, assumptions, limitations, and known gaps

This document prevents strong architecture language from being mistaken for an unconditional guarantee.

## What the implementation establishes

Within the tested single-process/reference-tool model:

- a journal-capable coordinator records forward and reverse intent before invoking a tool;
- journal transitions use compare-and-set semantics;
- successful committed steps publish idempotency keys;
- previously completed steps compensate in reverse order after a failure;
- compensation failure becomes explicit and dead-lettered;
- typed policies are mandatory for registered mutating tools;
- unsupported custom tools are not executed by name;
- file paths are canonicalized and constrained to a configured workspace;
- production configuration rejects host execution fallback;
- finite compensation contracts can be exhaustively checked over their declared domains;
- memory decay and clustering are deterministic for fixed data and parameters;
- absent optional backends can fail closed when `REQUIRE_BACKENDS=true`.

## Assumptions behind those statements

- The state backend is actually durable and available.
- A tool's declared compensation is correct and safe to retry.
- External systems honor idempotency or provide reliable effect-resolution APIs.
- Registry metadata truthfully describes mutation and capabilities.
- The supplied policy/invariant expresses the property humans intended.
- The executable implementation corresponds to the identity used in a proof certificate.
- Tenant checks are reached through the REST interface rather than bypassed in-process.
- Operators monitor and resolve dead letters.

## Explicit non-guarantees

- No ACID isolation across tools.
- No automatic reversal of irreversible effects.
- No guarantee that compensation erases externally observed consequences.
- No proof of arbitrary program behavior from SMT or bounded contracts.
- No hostile-Python-code containment from the subprocess worker.
- No distributed exactly-once execution.
- Only approval-pending Sagas are reconstructed into the live coordinator after restart;
  other terminal/history views are still primarily served from live process state.
- No demonstrated improvement to real LLM-agent task success.
- No scientifically validated model of human memory.
- No semantic quality guarantee for deterministic fallback embeddings or cluster labels.

## Known implementation boundaries

### Generic database mutation is intentionally absent

The former success-only `DATABASE_QUERY` reference stub was removed. Database effects
must be implemented as domain-specific registered tools with parameterized operations,
typed policies, idempotency, effect resolution, and exact compensation semantics.

### Restart observability differs from durable recovery

Startup can compensate incomplete durable effects, but completed/historical Saga aggregates are not loaded into `coordinator.active_sagas`. REST status/history first require an in-memory Saga lookup, so pre-restart IDs return 404 even if history exists durably.

### gRPC is not parity transport

It lacks durable coordinator wiring, authentication, typed REST schemas, rich argument types, and true live streaming.

### Temporal wrapping is coarse

The whole Saga runs inside one activity. Activity retry plus non-idempotent external effects can duplicate work. Durable per-step workflow modeling and external idempotency are needed for stronger distributed behavior.

### Speculation is validation, not a copy-on-write branch

Drafts pass the same side-effect-free registry, typed-policy, and capability preflight.
The selected draft is executed through the normal journaled Saga path. SagaMind does not
claim filesystem-overlay speculation or parallel execution of external effects.

### Health and readiness are intentionally separate

`/health` reports process liveness and backend modes. `/ready` fails with HTTP 503 when
production-required durable backends, Z3, or the isolated worker are unavailable. WASI
availability is reported separately from built-in worker isolation.

### Exact file rollback is bounded to text files

The REST path captures text preimages up to 1 MB, uses hash/absence preconditions, and
atomically replaces files. Binary files, very large files, directories, ACLs, extended
attributes, and externally observed writes require a domain-specific tool.

### Dead-letter tenant exposure

The dead-letter endpoint returns all records to any valid unrestricted or tenant-bound API key because it does not filter by tenant and dead-letter records do not include tenant ID.

## Suggested order for closing gaps

1. Add external-effect resolver/idempotency interfaces for each new production tool.
2. Make durable terminal/history reads and dead letters fully tenant-aware.
3. Either bring gRPC to REST security/validation parity or keep it disabled externally.
4. Model optional Temporal execution per durable step before using it for non-idempotent effects.
5. Add true copy-on-write adapters only if claiming speculative state branches.
6. Obtain unaffiliated replication and real deployment evidence before stronger claims.

## How to evaluate future claims

For each claim, ask:

1. What exact code path enforces it?
2. Does it fail closed when a dependency disappears?
3. What durable record proves it happened?
4. What crash boundary could make the outcome ambiguous?
5. What test injects that failure?
6. Does the experiment use a real external system or an in-memory model?
7. Is the claim local, bounded, end-to-end, or distributed?

This discipline is central to understanding SagaMind: the project is strongest when every safety statement includes its model and runtime assumptions.
