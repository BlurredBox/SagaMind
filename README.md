# SagaMind

[![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Harut200/SagaMind/blob/main/LICENSE)
[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-green.svg)](https://python.org)
[![gRPC](https://img.shields.io/badge/gRPC-v1.54-orange.svg)](https://grpc.io)
[![Z3 Solver](https://img.shields.io/badge/Z3%20SMT-v4.12-blueviolet.svg)](https://github.com/Z3Prover/z3)
[![WebAssembly](https://img.shields.io/badge/WebAssembly-Wasmtime-red.svg)](https://wasmtime.dev)

> **SagaMind** is a transaction-safe multi-agent runtime and tiered memory co-processor. It bridges the gap between *probabilistic* LLM reasoning and *deterministic* software engineering reliability: every agent action is formally verified by an SMT solver before it touches the host, every workflow can roll itself back, and agent memory decays and consolidates like a biological one.

📖 **Read the architecture deep-dive on Medium:** [SagaMind: Formal Verification, Transactional Rollback, and Cognitive Memory for LLM Agents](https://kesablyanharut.medium.com/sagamind-formal-verification-transactional-rollback-and-cognitive-memory-for-llm-agents-d5d186c5891f)

---

## Why SagaMind?

Deploying multi-agent networks in production environments is bottlenecked by two failures:

1. **State corruption (brittle execution).** When an agent executes a sequence of API, file, or database commands and fails at step 8, the environment is left half-mutated and corrupted.
2. **Context bloat (goldfish memory).** Agents carry flat vector buffers of conversation logs, leading to context-window pollution, rising costs, and hallucination.

SagaMind resolves both by combining an **Agentic Saga Transaction Protocol**, a **neuro-symbolic Z3 safety gate**, and a biologically inspired **tiered memory consolidation engine** ("sleep cycles").

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

- **Neuro-symbolic safety gate.** Every action an agent proposes is compiled into logical assertions and checked against safety invariants by the Z3 SMT solver *before* execution. The solver attempts to prove a violation is impossible (UNSAT); if it instead finds a counter-example (SAT) — or returns `unknown` — the action is rejected. Fail-closed by design.
- **Transactional Saga execution.** Agent workflows are treated as transactions: each step registers a compensating action, and on mid-workflow failure the engine rolls back in LIFO order, restoring environment consistency automatically.
- **Tiered memory consolidation.** Active recall is gated by Ebbinghaus forgetting curves (retention strictly decreasing in time, strictly increasing in retrieval count). A background "sleep cycle" DBSCAN-clusters episodic logs from TimescaleDB and distills them into a Neo4j semantic concept graph.
- **Speculative tool execution.** Agents can draft multiple execution paths in parallel inside temporary copy-on-write WebAssembly sandboxes; the winning path's state overlay is merged, the rest are discarded. This can substantially reduce latency for multi-path plans by trading idle wait time for parallel exploration.

---

## Comparison with Existing Frameworks

| Feature | LangGraph / CrewAI | Mem0 / Cognee | **SagaMind** |
| --- | --- | --- | --- |
| **Transaction safety** | No — DAGs only, no rollback | No | **Yes — Saga compensations** |
| **Active memory decay** | No — linear context growth | No — static retrieval | **Yes — Ebbinghaus math** |
| **Graph consolidation** | No | Partial — no sleep cycle | **Yes — DBSCAN sleep distillation** |
| **Formal invariants** | No | No | **Yes — Z3 SMT invariant proving** |
| **Speculative execution** | No — sequential | No | **Yes — parallel COW sandboxing** |

These frameworks optimize for developer velocity, and they are good at it. SagaMind optimizes for a different layer: provable boundaries on stochastic systems.

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

# ...or install everything for local development (dashboard, wasm, grpc, dev tools)
pip install -e ".[dev,dashboard,wasm,grpc,llm]"
```

All external backends (TimescaleDB, Neo4j, wasmtime, Z3, OpenAI) degrade gracefully to in-memory or deterministic fallbacks — the API, the dashboard, and the full test suite run with **no services configured**.

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

For exhaustive theoretical and technical specifications:

- [research_paper.md](https://github.com/BlurredBox/SagaMind/blob/main/research_paper.md) — theoretical foundations, CLS memory model, and SMT solving invariants.
- [system_architecture.md](https://github.com/BlurredBox/SagaMind/blob/main/system_architecture.md) — subsystem interactions, WebAssembly COW sandbox configs, and sequence flows.
- [specifications.md](https://github.com/BlurredBox/SagaMind/blob/main/specifications.md) — SQL schemas, Neo4j graphs, gRPC proto, and core algorithms.
- [architecture_exp.md](https://github.com/BlurredBox/SagaMind/blob/main/architecture_exp.md) — complete system specification with a full runnable code engine.

---

## Honest Limitations

- Z3's string theory is powerful but not complete; the verifier treats `unknown` results as rejections (fail-closed) and relies on path canonicalization preceding verification.
- If Z3 is unavailable in a constrained container, the verifier degrades to a deterministic string gate — weaker, and explicit about being weaker.
- Compensation actions can themselves fail; the coordinator deliberately halts and escalates to a human rather than attempting automated recovery of failed recovery.

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](https://github.com/BlurredBox/SagaMind/blob/main/CONTRIBUTING.md). If the premise resonates — that an agent's intelligence should be bounded by what it can prove, not what it can generate — issues and PRs are open.

## License

MIT — see the [LICENSE](https://github.com/BlurredBox/SagaMind/blob/main/LICENSE) file.

## Author

**Harutyun Kesablyan** — Co-founder at [BlurredBox](https://github.com/BlurredBox) (rollback-first ML governance) · [Medium](https://kesablyanharut.medium.com) · Yerevan, Armenia
