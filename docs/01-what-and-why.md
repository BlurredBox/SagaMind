# What SagaMind is, why it exists, and who needs it

## The problem

An LLM can produce a plausible sequence of commands, but plausibility is not transactionality. Suppose an agent must create a customer record, write a configuration file, reserve inventory, and notify a third party. If the fourth action fails, the first three effects may remain. Retrying can duplicate them. Restarting the process can lose the knowledge needed to undo them. A normal database transaction cannot atomically cover a filesystem, an API, a queue, and a remote SaaS product.

Long-running agents have a second problem: retaining every observation forever makes retrieval noisy and expensive. A flat list or vector index can recall similar text, but it does not by itself decide which memories are still useful or derive stable concepts from repeated experiences.

SagaMind explores both problems in one runtime:

- **recoverable action execution** for heterogeneous side effects;
- **tiered memory** for episodic evidence and derived semantic relationships.

## What the name means

**Saga** refers to the distributed-systems Saga pattern: a long-lived transaction is split into local steps, and every successfully applied step has a compensating action. If a later step fails, compensations run in reverse order.

**Mind** refers to the memory subsystem: individual events are kept as episodic records, assigned a retention score inspired by the Ebbinghaus forgetting curve, and periodically grouped into semantic concepts. This is an engineering metaphor, not a claim that the system reproduces biological memory.

## The role SagaMind plays

SagaMind sits between an agent/planner and effectful tools.

```text
user goal
   ↓
planner or agent (outside SagaMind)
   ↓ proposes action + inverse + invariant
SagaMind
   ├─ validates the request and tenant
   ├─ checks policy/invariants
   ├─ records recovery intent
   ├─ executes the allow-listed tool
   ├─ commits or compensates
   └─ exposes status, history, and operator failures
   ↓
filesystem / database adapter / WASI tool / other external system
```

SagaMind does not decide the user's goal, invent a complete plan, or reason conversationally. Its core value is making proposed effects more governable.

## Why an ordinary database transaction is insufficient

ACID transactions work inside one transactional resource. Agent workflows commonly cross resources with no shared transaction coordinator. SagaMind therefore aims for **eventual consistency with explicit compensation**, not ACID isolation.

This distinction matters:

- a compensation is a new action, not time travel;
- another observer may see the forward effect before compensation;
- a compensation can fail;
- some actions—sending an email, publishing data, charging a card without a refund API—are irreversible or only partially reversible;
- concurrent actors may change the world between the forward and reverse actions.

SagaMind makes these cases visible through explicit states and dead letters. It cannot make an irreversible operation reversible merely because a reverse tool name was supplied.

## Who benefits

SagaMind is relevant to teams building:

- agents that modify files, databases, infrastructure, tickets, or business records;
- multi-step automations where partial completion is costly;
- systems that require auditability, idempotency, human approval, and recovery after crashes;
- research prototypes studying formal gates, compensation protocols, or memory consolidation;
- agent gateways that need a narrow, policy-controlled execution surface.

It is less appropriate when all work already occurs inside one database transaction, when actions have no meaningful compensation, or when the application only needs a chatbot with no side effects.

## What ships today

The default Python runtime contains:

- a FastAPI server;
- a single-process Saga coordinator;
- memory, Redis, and PostgreSQL state-store implementations;
- a crash-consistent effect journal;
- typed tool policies and an optional Z3 SMT gate;
- a bounded compensation-contract DSL and verifier;
- a registry of four reference tools: `WRITE_FILE`, `DELETE_FILE`, `RESTORE_FILE`, and `NOOP`;
- a short-lived Python worker for trusted built-ins and optional Wasmtime/WASI execution;
- TimescaleDB/pgvector episodic storage with an in-memory fallback;
- Neo4j semantic relationships with an in-memory fallback;
- decay-filtered retrieval, deterministic cosine-DBSCAN consolidation, and optional LLM cluster labels;
- synchronous HTTP SDK and MCP adapter;
- optional gRPC and Temporal modules;
- Prometheus metrics and optional OpenTelemetry spans.

## What does not ship as a complete capability

- There is no built-in planner or LLM-agent loop.
- Generic database mutation is intentionally absent; production integrations must register a domain-specific,
  parameterized tool with an exact compensation contract.
- Speculative execution does not create copy-on-write sandboxes; it validates candidates without effects and executes only the selected candidate.
- Compensation certificates prove a declared finite model, not arbitrary Python, WASM, or third-party behavior.
- The Python worker is not a hostile-code container or microVM.
- Live PostgreSQL, Redis, Neo4j, Temporal, gRPC, and WASI behavior depends on optional services and dependencies.

## The central design philosophy

SagaMind treats generated actions as **untrusted proposals**. Safety is composed from multiple imperfect layers:

```text
API schema → typed policy / SMT → write-ahead journal → capability check
→ isolated execution boundary → result handling → commit or compensation
→ durable terminal state or explicit operator escalation
```

No single layer is described as sufficient. The system favors fail-closed behavior at trust boundaries and explicit uncertainty after crashes.
