# Interfaces, configuration, deployment, and operations

## REST API

The FastAPI application is the most complete public interface. Interactive OpenAPI is available at `/docs` when running.

| Method and path | Purpose | Notes |
| --- | --- | --- |
| `GET /health` | Process liveness and backend-mode report | Public; liveness does not imply readiness. |
| `GET /ready` | Production dependency readiness | Public; returns 503 when required services or execution boundaries are unavailable. |
| `GET /metrics` | Prometheus exposition | Public. |
| `POST /saga/start` | Create an in-memory and persisted Saga | Body: `tenant_id`, `goal`. |
| `POST /saga/step` | Validate and execute one step | File preimages and rollback contracts are derived server-side. |
| `GET /saga/{id}/status` | Current in-process Saga snapshot | Not reconstructed after restart. |
| `POST /saga/{id}/approve` | Approve pending step and resume | Pending steps are restored from durable state after restart. |
| `POST /saga/{id}/reject` | Reject pending work and compensate | Protected and tenant checked. |
| `GET /saga/{id}/history` | Ordered successful forward-step history | Reads the selected Saga-state backend. |
| `GET /saga/{id}/stream` | Server-Sent Events on status changes | Polls in-memory state every 0.5 seconds. |
| `GET /saga/dead-letters` | Operator failure queue | Tenant-bound keys see only their tenant. |
| `POST /memory` | Ingest episodic memory | Embeds, validates, and stores one tenant-scoped memory. |
| `POST /memory/consolidate` | Queue one tenant's sleep cycle | FastAPI background task, not a durable job queue. |
| `GET /memory/active` | Decay-filtered episodic retrieval | Optional semantic query, pagination 1–200. |
| `POST /speculative/run` | Validate candidates concurrently and execute the winner | Winner commits through the normal journaled Saga path. |

All non-health/metrics routes use API-key and in-process rate-limit dependencies.

### Example lifecycle

```bash
curl -X POST http://localhost:8000/saga/start \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: development-key' \
  -d '{"tenant_id":"acme","goal":"Create generated report"}'

curl -X POST http://localhost:8000/saga/step \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: development-key' \
  -d '{
    "saga_id":"<returned-id>",
    "step_name":"write report",
    "tool_name":"WRITE_FILE",
    "arguments":{"path":"reports/generated.txt","content":"hello"},
    "compensation_tool":null,
    "compensation_arguments":{},
    "invariants":"",
    "idempotency_key":"report-v1",
    "requires_approval":false
  }'
```

An empty invariant is permitted; the built-in typed policy and path/capability checks still apply.

## Authentication and tenant binding

`API_KEYS` is comma-separated. `secret` is unrestricted; `secret:tenant-a` binds that key to one tenant. Authentication is disabled in development if the list is empty and mandatory in production.

The rate limiter is process-local and keyed by API key or client address. It behaves as a fixed/sliding-list 60-second window despite the class name `SlidingRateLimiter`. Multiple API workers do not share counters.

Request size enforcement rejects oversized or malformed `Content-Length` values and explicitly measures bodies when that header is absent.

## Python SDK and MCP

`sdk/client.py` is a synchronous `httpx` wrapper around REST. It handles status errors and offers methods for Saga lifecycle, history, SSE lines, dead letters, memory retrieval/consolidation, speculation, and health.

`sdk/mcp_server.py` exposes most of those methods as MCP tools so an MCP-capable agent can drive SagaMind. It is an adapter; enforcement remains in the REST server. The MCP surface does not currently expose speculative execution or Saga status streaming.

## gRPC

The optional protobuf service supports starting a Saga, submitting a step, reading status, and a server-streaming step method. Generated stubs are committed and CI regenerates/import-checks them.

Important differences from REST:

- map values are strings, so argument types are less expressive;
- there is no REST-equivalent request-schema validation;
- it enforces API-key and tenant binding but does not yet share every REST schema helper;
- the standalone server uses `SagaStateStore` and runs startup recovery;
- production requires TLS certificate/key configuration; development may bind insecurely;
- its “stream” executes synchronously, buffers callback events in a list, then yields them after execution rather than streaming live.

It is therefore a prototype transport, not security/semantics parity with REST.

## Temporal

The optional Temporal worker exposes `SagaWorkflow`, which runs one activity wrapping the coordinator. The module is not invoked by REST. Each activity creates a new state store and starts a new Saga record. Temporal can retry an activity, but safe retries of real external effects still require idempotent tools/resolution; workflow durability alone does not remove the coordinator's ambiguous-effect problem.

## Configuration reference

All settings are environment-driven through `pydantic-settings`; explicit constructor values override environment, which overrides `.env`, which overrides defaults.

### Server and security

| Variable | Default | Meaning |
| --- | --- | --- |
| `ENV` | `development` | `production` activates strict validation. |
| `HOST` / `PORT` / `GRPC_PORT` | `0.0.0.0` / `8000` / `50051` | Listener configuration. |
| `GRPC_TLS_CERT` / `GRPC_TLS_KEY` | empty | Paired PEM paths; required when starting gRPC in production. |
| `API_KEYS` | empty | Comma-separated keys, optionally `key:tenant`. |
| `CORS_ORIGINS` | empty | Browser origin allow-list. |
| `RATE_LIMIT_PER_MINUTE` | `0` | Zero disables limiting. |
| `MAX_REQUEST_BYTES` | `1048576` | `Content-Length` threshold. |
| `ALLOWED_WORKSPACE_ROOT` | current working directory | Absolute filesystem jail root. |

### Execution

| Variable | Default | Meaning |
| --- | --- | --- |
| `SANDBOX_EXECUTION_MODE` | `isolated` | `isolated` worker or explicit development `host`. |
| `SANDBOX_ALLOW_HOST_FALLBACK` | `false` | Required for host execution; forbidden in production. |
| `SANDBOX_WORKER_TIMEOUT_S` | `10` | Parent wall-clock timeout. |
| `SANDBOX_MEMORY_LIMIT_MB` | `256` | Upper bound; Linux worker/WASI enforcement differs. |
| `SANDBOX_FUEL_LIMIT` | `1000000` | WASI fuel limit. |
| `SANDBOX_MAX_OUTPUT_BYTES` | `65536` | Worker-output cap. |
| `Z3_TIMEOUT_MS` | `5000` | SMT check timeout. |

### Stores and memory

| Variable family | Purpose |
| --- | --- |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASS` | PostgreSQL/Timescale connection. |
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASS`, `NEO4J_TIMEOUT_S` | Semantic graph. |
| `REDIS_HOST`, `REDIS_PORT` | Alternate Saga-state backend. |
| `STATE_STORE_BACKEND` | Force `memory`, `redis`, or `postgres`; empty auto-detects. |
| `REQUIRE_BACKENDS` | Fail instead of using in-memory fallbacks. |
| `OPENAI_API_KEY`, `EMBEDDING_MODEL`, `EMBEDDING_DIM` | Embedding service. |
| `CONSOLIDATION_MODEL` | Declared setting; default wiring does not construct an LLM label client. |
| `CONSOLIDATION_CRON` | Five-field optional schedule. |
| `HNSW_M`, `HNSW_EF_CONSTRUCTION`, `HNSW_EF_SEARCH` | Vector index/search tuning. |

### Distributed execution

`TEMPORAL_TARGET`, `TEMPORAL_NAMESPACE`, and `TEMPORAL_TASK_QUEUE` configure the optional worker.

Production startup rejects known development database/Neo4j passwords, missing API keys, host sandbox mode/fallback, `REQUIRE_BACKENDS=false`, and any Saga store other than PostgreSQL.

## Deployment topology

`docker-compose.yml` runs TimescaleDB, Neo4j, Redis, and the SagaMind container. The application container is read-only except for `/tmp` and a dedicated workspace volume and uses `no-new-privileges`. Compose requires secrets through environment expansion.

The current compose service publishes ports 8000 and 50051, but its container command determines which server actually starts; the Dockerfile should be checked before assuming both REST and gRPC are running.

## Observability

Prometheus counters cover Saga starts, commits, rollbacks, compensation failures, and rejected steps. Histograms cover verification and step latency. If `prometheus-client` is absent, metrics become no-ops.

OpenTelemetry remains optional. HTTP requests, verification, tool execution, and compensation create spans when it is installed. Logging supports JSON in production and colored output otherwise; REST responses and downstream log records share the same `X-Request-ID`.

## Operator checklist

For a production-like deployment:

1. use strong, non-default secrets and tenant-bound keys where appropriate;
2. set `ENV=production` and `REQUIRE_BACKENDS=true`;
3. mount a narrow workspace root;
4. keep host fallback disabled;
5. make forward actions and compensations externally idempotent;
6. implement effect and compensation resolvers for external systems;
7. monitor dead letters and compensation-failure metrics;
8. run live integration tests against the chosen databases;
9. validate backup/restore and migration procedures;
10. do not expose the prototype gRPC server as though it had REST-equivalent controls.
