# Codebase map and responsibility index

## Root-level sources

| Path | Responsibility | Status |
| --- | --- | --- |
| `README.md` | Product-level introduction and quick start. | Current overview. |
| `ARCHITECTURE.md` | Concise source of truth for implemented architecture and non-guarantees. | Authoritative narrative. |
| `ROADMAP.md` | Acceptance criteria and recorded completion evidence. | Project/release record. |
| `research_paper.md` | Protocol, experiment results, and validity limits. | Research artifact. |
| `specifications.md` | Schemas, algorithms, interfaces. | Mixed detailed specification. |
| `system_architecture.md` | Earlier/current-target architecture material. | Subordinate to code and `ARCHITECTURE.md`. |
| `architecture_exp.md` | Large design/reference implementation narrative. | Historical/target material. |
| `improve.md` | Extensive improvement analysis. | Planning/review material. |
| `app_demo.py` | Streamlit visualization of transactions, memory decay, and verification. | Demo, not core API. |
| `docker-compose.yml` / `Dockerfile` | Reference container topology and application image. | Deployment scaffolding. |
| `Makefile` | Common install, test, lint, research, server, and migration commands. | Developer entry point. |

## Core runtime

| Path | Main types/functions | Why it exists |
| --- | --- | --- |
| `src/main.py` | FastAPI app, schemas, dependencies, endpoints, lifecycle | Public REST composition root. |
| `src/config.py` | `Settings` | One environment-backed configuration source and production fail-closed checks. |
| `src/models.py` | `SagaStatus`, `StepStatus`, `ActionPayload`, `SagaStep`, `SandboxResult`, `MemoryNode`, `SagaTransaction` | Shared internal domain vocabulary. |
| `src/security.py` | `contain_path`, `SlidingRateLimiter` | Framework-light security primitives. |
| `src/logging_config.py` | JSON/color formatters | Environment-sensitive logging. |
| `src/observability/metrics.py` | `Metrics`, `span` | Optional Prometheus/OpenTelemetry facade. |

## Orchestration

| Path | Responsibility |
| --- | --- |
| `src/orchestrator/coordinator.py` | Forward execution, invariant gate, approval pause/resume, idempotency checks, journal transitions, compensation, startup recovery, dead-letter escalation. |
| `src/orchestrator/state_store.py` | Memory/Redis/PostgreSQL Saga state, effect journal, compare-and-set transitions, history, idempotency, incomplete-Saga discovery. |
| `src/orchestrator/sandbox.py` | Tool/capability registry, built-in handlers, typed-policy enforcement, Python worker protocol, WASI execution. |
| `src/orchestrator/sandbox_worker.py` | Child-process entry point and OS resource limits. |
| `src/orchestrator/temporal_worker.py` | Optional Temporal workflow/activity wrapper. |

## Verification and contracts

| Path | Responsibility |
| --- | --- |
| `src/policy.py` | Typed argument policy model, evaluation, structured rejections, built-in policies. |
| `src/verifier/z3_prover.py` | SMT-LIB2 refutation checks, fallback containment check, structured result/repair hints. |
| `src/contracts/dsl.py` | Finite typed language for reversible state transitions. |
| `src/contracts/verifier.py` | Exhaustive bounded verification, counterexamples, repair constraints, certificates and hashes. |

There are two different verification concepts: action invariants (`Z3Verifier`) check one concrete proposal before execution; compensation contracts (`BoundedContractVerifier`) analyze every value in a declared finite model, normally before tool registration.

## Memory

| Path | Responsibility |
| --- | --- |
| `src/memory/embedding.py` | OpenAI embeddings or deterministic non-semantic fallback, plus LRU cache. |
| `src/memory/timescale_store.py` | Episodic writes, cosine retrieval, SQL decay filtering, in-memory fallback. |
| `src/memory/decay.py` | Python retention equation and keep/prune partition. |
| `src/memory/consolidation.py` | Pairwise distances, deterministic DBSCAN, optional cluster labels, graph projection. |
| `src/memory/neo4j_store.py` | Concept/relationship reads and writes, retry/timeout, in-memory fallback. |
| `native/sagamind_native/src/lib.rs` | Optional PyO3 pairwise cosine-distance acceleration. |

## Alternative interfaces

| Path | Responsibility |
| --- | --- |
| `sdk/client.py` | Synchronous REST client. |
| `sdk/mcp_server.py` | MCP tools backed by the REST client. |
| `proto/sagamind.proto` | Optional gRPC contract; generated Python bindings are committed under `src/generated/`. |
| `src/grpc_server.py` | Optional async gRPC server. |
| `scripts/gen_proto.sh` | Generate Python protobuf stubs into `src/generated`. |

## Database definition sources

There are three schema mechanisms:

- `migrations/001_init.sql`: Docker first-boot schema, including Timescale hypertable and effect journal;
- `alembic/versions/0001_initial_schema.py`: managed migration path;
- runtime `CREATE TABLE IF NOT EXISTS` logic in the Timescale and Saga stores.

These must be kept aligned. Runtime creation is convenient but is not a substitute for reviewed migrations in production.

## Tests as executable documentation

The tests are organized by component. Particularly useful reading:

- `test_effect_journal.py`: effect transitions and crash windows;
- `test_recovery.py`: restart compensation;
- `test_coordinator.py` and `test_coordinator_edges.py`: Saga lifecycle and edge cases;
- `test_policy.py`, `test_z3_prover.py`, `test_repair.py`: policy outcomes and repair data;
- `test_contracts.py` and `test_property_based.py`: bounded proof semantics;
- `test_sandbox.py` and `test_sandbox_edges.py`: capability and worker enforcement;
- `test_decay.py`, `test_consolidation.py`, `test_timescale_store.py`: memory equations and clustering/storage;
- `tests/integration`: optional live-service behavior.

## Experiments

`experiments/` evaluates component behavior and ablations with seeded trials. It records raw CSVs and a JSON summary. These experiments support claims about the tested synthetic/file workloads; they do not show end-to-end LLM task success or prove production distributed consistency.

## A practical trace for new contributors

To follow one REST step through the source:

```text
src/main.py::submit_step
  → src/models.py::SagaStep
  → src/orchestrator/coordinator.py::execute_saga
  → src/verifier/z3_prover.py::verify
  → src/orchestrator/state_store.py::prepare_effect/transition_effect
  → src/orchestrator/sandbox.py::WasmSandbox.execute
  → src/policy.py::ToolPolicyRegistry.require
  → src/security.py::contain_path (for paths)
  → src/orchestrator/sandbox_worker.py
  → src/orchestrator/sandbox.py::worker_execute + built-in handler
  → state_store APPLIED/COMMITTED + history
```
