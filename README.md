# SagaMind

[![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/BlurredBox/SagaMind/blob/main/LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-green.svg)](https://python.org)
[![gRPC](https://img.shields.io/badge/gRPC-v1.84-orange.svg)](https://grpc.io)
[![Z3 Solver](https://img.shields.io/badge/Z3%20SMT-v4.12-blueviolet.svg)](https://github.com/Z3Prover/z3)
[![WebAssembly](https://img.shields.io/badge/WebAssembly-Wasmtime-red.svg)](https://wasmtime.dev)

> **SagaMind** is a research prototype for recoverable multi-agent tool execution and tiered memory. It combines a crash-consistent effect journal, mandatory typed policies for built-in mutations, bounded compensation proofs, isolated workers, parameterized memory decay, and density-based consolidation. Every guarantee is explicitly bounded by its model and runtime assumptions.

📖 **Read the architecture deep-dive on Medium:** [SagaMind: Formal Verification, Transactional Rollback, and Cognitive Memory for LLM Agents](https://kesablyanharut.medium.com/sagamind-formal-verification-transactional-rollback-and-cognitive-memory-for-llm-agents-d5d186c5891f)

---

## Why SagaMind?

Deploying multi-agent networks in production environments is bottlenecked by two failures:

1. **State corruption (brittle execution).** When an agent executes a sequence of API, file, or database commands and fails at step 8, the environment is left half-mutated and corrupted.
2. **Context bloat (goldfish memory).** Agents carry flat vector buffers of conversation logs, leading to context-window pollution, rising costs, and hallucination.

SagaMind addresses both by combining an **Agentic Saga Transaction Protocol**, a **neuro-symbolic Z3 safety gate**, and a biologically inspired **tiered memory consolidation engine** ("sleep cycles").

```
                  ┌─────────────────────────────────────┐
                  │           SagaMind Engine           │
                  └──────────────────┬──────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│   Transaction   │         │  Tiered Memory  │         │ Neuro-Symbolic  │
│  Orchestrator   │         │  Co-Processor   │         │    Verifier     │
│ (Saga Pattern)  │         │ (CLS & Sleep)   │         │ (Z3 SMT Solver) │
└─────────────────┘         └─────────────────┘         └─────────────────┘
```

---

## Features

- **Typed policy boundary.** Built-in mutating tools cannot register without a typed argument policy. Missing, nested, wrongly typed, out-of-range, extra, and path-escaping values fail closed with structured repair constraints. Raw SMT-LIB2 remains an explicit advanced layer; timeout, `unknown`, invalid input, and unsupported values reject.
- **Crash-consistent Saga execution.** The write-ahead effect journal persists action and inverse before execution, advances through `PREPARED → EXECUTING → APPLIED → COMMITTED`, and records compensation separately. Ambiguous crash outcomes are resolved or dead-lettered—never silently called restored.
- **Verified compensation contracts.** A bounded DSL declares precondition, forward transition, postcondition, compensation, and restoration invariant. Certificates bind the complete model and result to a declared tool implementation identity. Unsupported, vacuous, unbounded, or irreversible contracts do not receive a proved status.
- **Isolated execution.** Bundled tools run in short-lived workers with filesystem, network, environment, CPU/time, memory, and output capabilities. Production rejects unavailable isolation and host fallback. WASI remains available for untrusted compiled tools.
- **Tiered memory consolidation.** A parameterized exponential score prioritizes episodic records; deterministic cosine-DBSCAN groups dense memories before optional concept labeling. The score is biologically inspired, not a validated model of human memory.
- **Speculative validation.** Candidate actions are validated concurrently without side effects; only the selected valid action executes. Copy-on-write filesystem overlays are not implemented.

---

## Design-scope comparison

| Feature | LangGraph / CrewAI | Mem0 / Cognee | **SagaMind** |
| --- | --- | --- | --- |
| **Compensation coordinator** | Framework-dependent | Not primary scope | **Implemented** |
| **Parameterized memory decay** | Framework-dependent | Product-dependent | **Implemented** |
| **Density-based consolidation** | Framework-dependent | Product-dependent | **Implemented** |
| **Typed mutation policies + SMT escape hatch** | Framework-dependent | Not primary scope | **Implemented** |
| **Bounded compensation certificates** | Framework-dependent | Not primary scope | **Implemented** |
| **Crash-consistent effect journal** | Framework-dependent | Not primary scope | **Implemented** |
| **Speculative validation** | Framework-dependent | Not primary scope | **Implemented; no COW overlay** |

This table states SagaMind's implemented scope, not a current empirical audit of every version of other projects. See their documentation before making product-selection claims.

## Reproducible evidence

Run `make research` to reproduce controlled fault injection, SMT classification, real-filesystem ablations, failure-semantics checks, synthetic clustering, and decay-property results. Run `./external_validation/run_replication.sh` from a clean Python 3.11 checkout to execute the frozen 1,000-run v2 comparison against native and matched-control Temporal 1.33.0 and LangGraph 1.2.12 configurations. Both suites emit raw trials and machine-readable summaries. See [experiments/README.md](experiments/README.md), [external_validation/REPORT.md](external_validation/REPORT.md), and [research_paper.md](research_paper.md).

Current evidence validates component behavior and one narrow public-framework comparison only. It does not demonstrate improved end-to-end LLM-agent task success, real-world memory quality, general distributed consistency, or scientific novelty. The bundled author-run reproduction is not independent replication; an unaffiliated signed execution is still required.

---

## Quick Start

### 1. Prerequisite setup

Python 3.10+ and, optionally, the Z3 solver binary.

```bash
# Clone the repository
git clone https://github.com/BlurredBox/SagaMind.git
cd SagaMind

# Install the core runtime
pip install -e .

# Reproduce the locked Python 3.11 runtime used by the container
pip install -r requirements.lock

# ...or install everything for local development (dashboard, wasm, grpc, dev tools)
pip install -e ".[dev,dashboard,wasm,grpc,llm]"
```

Development and tests can use documented in-memory or deterministic fallbacks. Production configuration fails closed for missing required backends, insecure secrets, unavailable isolation, or enabled host fallback.

### 2. Launch the interactive dashboard demo

A visual dashboard showing live transaction rollback flows, memory decay values, and Z3 symbolic verification logs:

```bash
pip install -e ".[dashboard]"
streamlit run app_demo.py
```

### 3. Run the API or the full stack

```bash
make run                      # REST API on :8000 (OpenAPI at /docs)
docker compose up --build     # API + TimescaleDB + Neo4j + Redis
```

---

## Architecture Deep Dive

Current shipped behavior and its limits are defined in one place:

- [docs/README.md](docs/README.md) — start-to-finish handbook covering concepts, architecture, execution, safety, memory, interfaces, operations, code ownership, limitations, and terminology.
- [ARCHITECTURE.md](ARCHITECTURE.md) — authoritative implemented architecture and non-guarantees.
- [research_paper.md](research_paper.md) — frozen protocol, results, and validity limits.
- [ROADMAP.md](ROADMAP.md) — acceptance criteria for the pre-external-impact release.
- [system_architecture.md](system_architecture.md) — historical/current-target mix, explicitly subordinate to `ARCHITECTURE.md`.
- [specifications.md](specifications.md) — SQL schemas, Neo4j graphs, gRPC proto, and core algorithms.
- [architecture_exp.md](architecture_exp.md) — complete system specification with a full runnable code engine.

---

## Honest Limitations

- The bounded contract verifier proves only the declared finite model, not arbitrary external code; implementation identity binding detects a changed identity but does not prove semantic equivalence.
- Legacy raw SMT verifies only supplied invariants. The typed mutation-policy path rejects unsupported values and missing mutation policies.
- Compensation actions can themselves fail; the coordinator deliberately halts and escalates to a human rather than attempting automated recovery of failed recovery.
- The Python worker is a resource and process boundary around trusted built-ins, not a hostile-code container or microVM. Use WASI or an external sandbox for untrusted code.
- PostgreSQL, Redis, and Neo4j integration behavior still requires live-service testing in the deployment environment.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](https://github.com/BlurredBox/SagaMind/blob/main/CONTRIBUTING.md). If the premise resonates — that an agent's intelligence should be bounded by what it can prove, not what it can generate — issues and PRs are open.

## License

MIT — see the [LICENSE](https://github.com/BlurredBox/SagaMind/blob/main/LICENSE) file.

## Author

**Harutyun Kesablyan** — Co-founder at [BlurredBox](https://github.com/BlurredBox) (rollback-first ML governance) · [Medium](https://kesablyanharut.medium.com) · Yerevan, Armenia
