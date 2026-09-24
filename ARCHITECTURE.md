# SagaMind implemented architecture

This file is the source of truth for shipped behavior. `system_architecture.md`, `architecture_exp.md`, and `ABOUT/ABOUT.md` contain historical or target designs and cannot override this document.

## Safety model

SagaMind treats generated plans as untrusted proposals. Safety is layered:

```text
request schema
    ↓
typed tool policy / SMT obligation
    ↓
durable prepared effect + compensation intent
    ↓
capability-constrained executor
    ↓
postcondition and result validation
    ↓
commit or reverse-order compensation
    ↓
durable terminal state / explicit dead letter
```

No layer provides unconditional safety. Policy correctness, compensation semantics, isolation availability, and external-system behavior remain assumptions surfaced in results and status.

## Core components

### Coordinator

Owns Saga lifecycle, ordered step execution, approval gates, idempotency, reverse-order compensation, and recovery escalation. It must not claim restoration if any registered compensation fails.

### State and effect journal

Stores Saga state, step intent, effects, idempotency records, compensations, history, and dead letters. Durable backends are PostgreSQL and Redis; memory backend is for tests and local development.

### Policy verifier

Every registered built-in mutating tool carries a typed policy. The executor rejects missing, unsupported, nested, extra, wrongly typed, out-of-range, or path-escaping arguments before starting a worker. Advanced SMT policies check supported concrete arguments by refutation (`arguments ∧ ¬invariant`); `sat`, parse failure, timeout, and `unknown` reject with structured diagnostics.

### Tool executor

Routes allow-listed tools through capability definitions. Production must fail closed when required isolation is unavailable. Host fallback, if enabled for development, is a degraded mode rather than a sandbox claim.

### Compensation contracts

Describe typed finite domains, forward and reverse state transitions, and the restoration obligation. Certificates hash the full contract, result, and declared implementation identity. Tool registration can require a proved, identity-matching certificate. A certificate does not prove that arbitrary external code implements the model.

### Memory

Episodic records remain evidence. A parameterized exponential score prioritizes records, deterministic cosine-DBSCAN groups dense episodes, and optional labeling writes semantic relationships. Biological language is inspiration, not empirical validation.

### Evaluation

`experiments/` contains seeded component checks, deterministic crash/failure semantics, and four real temporary-filesystem ablations. Results include raw trials, confidence intervals, latency, environment, revision, and limitations. Hosted-model or public-benchmark runs remain separate because they add external variance and cost.

## Explicit non-guarantees

- Saga compensation is not ACID isolation.
- Irreversible third-party effects cannot be made reversible by naming a compensation.
- SMT verifies encoded properties, not human intent or policy completeness.
- Bounded contract proofs do not establish unbounded program equivalence.
- Path containment is not a general process-isolation boundary.
- Synthetic clustering quality does not predict natural-language memory quality.
- Single-process tests do not establish distributed correctness.
