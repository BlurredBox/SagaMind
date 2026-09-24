# SagaMind pre-external-impact completion plan

This is the execution contract for the original pre-external-impact goal. A phase is complete only when every acceptance criterion passes. Public launch, user acquisition, independent replication, and publication submission were intentionally excluded from phases 0–6. A later public-baseline and replication-readiness extension is recorded below.

## End goal

Deliver a reproducible agent runtime that can:

1. persist intent before an external effect;
2. recover deterministically after a crash at any lifecycle boundary;
3. reject actions that lack required typed policy coverage;
4. verify bounded compensation-restoration contracts;
5. execute built-in mutations behind an explicit worker boundary and untrusted compiled tools through WASI;
6. return structured counterexamples and repair constraints;
7. demonstrate each control's contribution through baselines and ablations; and
8. pass documented test, type, lint, security, and coverage gates.

## Phase 0 — credibility cleanup

**Goal:** Make every public claim traceable to code, test, or result artifact.

Acceptance criteria:

- [x] One current architecture source of truth (`ARCHITECTURE.md`).
- [x] Historical documents marked as design or historical material.
- [x] No invented citations, COW claims, or unconditional safety guarantees.
- [x] Reproducible evaluator records revision, dirty state, seed, environment, raw data, and scope.
- [x] Dependency audit fails CI on a real vulnerability finding.
- [x] Critical limitations appear in README and security documentation.

## Phase 1 — crash-consistent execution

**Goal:** No acknowledged side effect exists without durable recovery intent.

Acceptance criteria:

- [x] Durable lifecycle: `PREPARED → EXECUTING → APPLIED → COMMITTED`.
- [x] Compensation lifecycle persisted separately.
- [x] Intent and compensation recorded before forward execution.
- [x] Idempotency keys survive process restart.
- [x] Ambiguous effects enter explicit recovery state; never silently reported as restored.
- [x] Fault injection covers every transition before/after persistence and execution.
- [x] Recovery converges or produces dead-letter/human-action record.

## Phase 2 — mandatory typed policy boundary

**Goal:** Every mutating tool has a machine-checkable policy contract.

Acceptance criteria:

- [x] Typed policy schema; raw unvalidated SMT is an advanced escape hatch only.
- [x] Missing policy for mutating tool fails closed.
- [x] Nested/unsupported argument values are rejected or explicitly abstracted.
- [x] Timeout, parse failure, and `unknown` fail closed.
- [x] Result contains violated property, counterexample, and repair constraints.
- [x] Policy coverage and mutation tests exist for built-ins.

## Phase 3 — verified compensation contracts

**Goal:** “Reversible” means tested or proved against a declared restoration condition.

Acceptance criteria:

- [x] Contract declares precondition, forward transition, postcondition, compensation, and restoration invariant.
- [x] Bounded verifier checks the restoration obligation.
- [x] Unsupported/unbounded contracts cannot receive a verified status.
- [x] Certificate hashes contract, bounded proof inputs/result, and tool implementation identity.
- [x] Runtime can require a valid certificate before registering a mutating tool.
- [x] Property tests cross-check verifier results against executable add/subtract models.

## Phase 4 — isolated tool execution

**Goal:** Production mutation never falls back silently to unrestricted host execution.

Acceptance criteria:

- [x] Capability manifest covers filesystem, network, environment, CPU/fuel, memory, and output.
- [x] Production mode fails closed if required isolation is unavailable.
- [x] Development fallback is explicit, observable, and disabled by default in production.
- [x] Built-in mutating tools use isolated execution path or isolated worker boundary.
- [x] Escape, symlink, resource-exhaustion, and unavailable-runtime tests pass.

## Phase 5 — evidence and ablation

**Goal:** Quantify what each control contributes and what it costs.

Acceptance criteria:

- [x] Baselines: sequential, verify-only, Saga-only, full system.
- [x] Real temporary-filesystem workload plus deterministic synthetic component tests.
- [x] Failure classes include exception, false result, unsafe path, duplicate, crash boundary, and compensation failure.
- [x] Metrics include residual effects, unsafe executions, recovery rate, solver errors, and latency.
- [x] Confidence intervals/raw trials emitted where repeated sampling is used.
- [x] Paper results are generated from artifacts and limitations remain explicit.

## Phase 6 — release candidate gate

**Goal:** Produce strongest defensible pre-launch artifact.

Acceptance criteria:

- [x] Unit and property tests pass.
- [x] Live-backend integration suite is documented and runnable.
- [x] Ruff and mypy pass.
- [x] Critical modules (`coordinator`, `state_store` memory path, `sandbox`, `security`, `verifier`, contracts) reach at least 85% statement coverage.
- [x] Whole-source coverage floor remains honest and documented.
- [x] Dependency and secret scans are enforced.
- [x] Fresh `make research` run succeeds from an isolated source snapshot.
- [x] No goal item remains described as implemented unless its acceptance test passes.

## Completion evidence (24 September 2026)

- Tests: 274 passed; one live-backend integration collection skipped locally (final release gate).
- Coverage: 75.98% whole source; critical targets 88.14–100%; memory state-store path 89.89%.
- Static checks: Ruff formatting/lint and mypy passed.
- Supply chain: Python 3.11 lock generated; `pip-audit` found no known vulnerabilities; CI scan is fail-closed.
- Evaluation: 1,000 Saga trials, 1,000 SMT cases, 30 clustering datasets, 360 decay checks, 800 filesystem-ablation trials, and four deterministic failure-semantics cases.
- Isolated snapshot: `make research` reproduced 1,000 Saga rows, 800 ablation rows, four of four failure cases, and a 1.000 full-system safe-outcome rate.

## External-validation extension (24 September 2026)

- [x] Freeze a repository-local protocol before the first named-baseline run; disclose that it was not externally preregistered.
- [x] Execute identical opaque cases against integrated SagaMind plus native and matched-control Temporal 1.33.0 and LangGraph 1.2.12 configurations.
- [x] Score real filesystem state outside framework return values and publish all 1,000 raw rows.
- [x] Report Wilson intervals, paired exact McNemar tests, capability differences, and interpretation limits.
- [x] Produce one combined Python 3.11 dependency lock, source/protocol/corpus hashes, and a verified SHA-256 manifest.
- [x] Recalculate primary outcomes through a separate standard-library verifier.
- [x] Reproduce the complete one-command workflow from a source-only snapshot.
- [x] Provide an unaffiliated-executor attestation template that defaults to `independent_replication: false`.

The project-side replication work is complete. The author-run result is a validated self-replication, not independent replication. Independent status is an external fact and remains false until an unaffiliated executor runs the frozen bundle and signs the attestation.
