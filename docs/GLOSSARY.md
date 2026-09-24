# SagaMind glossary

## A–C

**Action** — A forward tool invocation represented by a tool name and arguments.

**Active memory** — An episodic memory whose computed retention score is at least `tau`. “Active” does not mean loaded into an LLM context automatically.

**Admissible case** — In bounded contract verification, a state/input combination satisfying the precondition.

**Agentic Saga Transaction Protocol** — Project terminology for applying Saga coordination, verification, journaling, compensation, and recovery to agent-proposed tool actions.

**AMBIGUOUS** — Journal state meaning an external call may or may not have taken effect and recovery cannot safely assume either outcome.

**APPLIED** — The forward tool reported success and its result was durably recorded, but final commit/idempotency publication has not yet completed.

**Bounded proof** — Exhaustive checking over explicitly finite variable domains. It is complete for that finite model, not for unbounded real-world behavior.

**Capability manifest** — A declaration of the filesystem, network, environment, fuel/CPU, memory, and output resources a tool may use.

**Certificate** — A structured record of bounded contract-verifier output, including contract, implementation-identity, and proof hashes.

**CLS** — Complementary Learning Systems, the neuroscience-inspired distinction between fast episodic learning and slower semantic consolidation. In SagaMind it is a design metaphor.

**Commit** — Final acknowledgement that an applied effect is accepted and its optional idempotency key is published.

**Compensation** — A new action intended to semantically reverse a completed forward action.

**COMPENSATED** — Journal state indicating the registered compensation reported success.

**COMPENSATING** — Journal state indicating reverse execution has begun; after a crash its outcome can be ambiguous.

**COMPENSATION_FAILED** — A step/Saga/effect condition in which reverse execution failed and human/operator resolution is required.

**Compensation contract** — Typed model containing precondition, forward transition, postcondition, compensation transition, and restoration condition.

**Consolidation** — Clustering related episodic memories and writing derived concept relationships to the semantic graph.

**Cosine distance** — `1 - cosine_similarity`; used by clustering and pgvector search. Lower means closer.

**Counterexample** — A concrete assignment showing that an invariant, postcondition, or restoration condition fails.

**Crash consistency** — Organizing durable writes so restart recovery can distinguish known states and explicitly quarantine unknown outcomes.

## D–H

**DBSCAN** — Density-Based Spatial Clustering of Applications with Noise; groups points using neighborhood density without choosing a cluster count in advance.

**Dead letter** — Operator-visible record of a Saga that automatic recovery could not safely complete.

**Deterministic embedding fallback** — Hash-derived unit vector used offline. Stable across runs, but not linguistically semantic.

**Effect** — An externally visible state change caused by a tool.

**Effect journal** — Durable write-ahead record of action, inverse, sequence, state, result, and error.

**Embedding** — Numeric vector representing text for similarity operations. Only the configured model-backed path is intended to encode semantics.

**Episodic memory** — Record of a particular experience/event with time, role, summary, importance, retrieval count, context, and embedding.

**Eventual consistency** — A model in which temporary divergence is allowed and later compensating actions aim to restore a valid state.

**Fail closed** — Reject or stop when safety cannot be established, rather than silently proceeding.

**Forward action** — The intended business operation before any rollback.

**GraphRAG** — Retrieval augmented with graph relationships. SagaMind attaches concept neighbors associated with an episode's `agent_role`; it is a small graph-enrichment path, not a full graph-reasoning engine.

**HNSW** — Hierarchical Navigable Small World graph, pgvector's approximate nearest-neighbor index used here for cosine search.

**Human-in-the-loop (HITL)** — Approval pause before a verified step is journaled and executed.

## I–P

**Idempotency** — Property that repeating an operation with the same key has the effect of one logical operation. SagaMind records committed keys, but external tools must also support safe crash-window behavior.

**Implementation identity** — Declared string whose hash is bound into a compensation certificate. It identifies a claimed implementation; it does not inspect its semantics.

**Invariant** — Property expected to hold for an action's concrete arguments. In the Saga path it is supplied as SMT-LIB2.

**Isolation** — Separation limiting what execution can affect. In SagaMind this may mean a short-lived worker or WASI; it does not mean ACID transaction isolation.

**LIFO rollback** — Last-in-first-out compensation order.

**Memory strength (`S`)** — Time constant computed from base strength, importance, and retrieval reinforcement.

**MCP** — Model Context Protocol; the adapter exposes SagaMind REST operations as tools to compatible clients.

**Modular monolith** — One deployable/process containing separated modules. This describes the default REST runtime.

**Mutation policy** — Typed rules required for a tool declared to modify state.

**Neuro-symbolic** — Combining learned/agent-generated proposals with symbolic checks. In this project the symbolic parts are typed validation, SMT, and bounded contracts.

**Noise point** — DBSCAN point that is not part of any dense cluster.

**Operator escalation** — Stopping automation and surfacing a dead letter rather than claiming recovery.

**Policy repair constraint** — Machine-readable suggestion describing how a rejected proposal could meet a rule, such as a bound or required type.

**Postcondition** — Contract property that must hold after the forward transition.

**PREPARED** — Effect and compensation have been durably recorded; external execution has not been marked as started.

**Precondition** — Contract condition selecting the initial states/inputs for which the transition is valid.

**Proof space** — Cartesian product of finite state and input domains checked by the bounded verifier.

## R–Z

**Recovery resolver** — Tool-specific mechanism that determines whether an interrupted forward or compensation call actually took effect.

**Redis fallback** — Shared Saga-state backend selected after PostgreSQL and before process memory in automatic mode.

**Restoration condition** — Contract property required after forward execution followed by compensation; it may express equality with original state or a weaker valid-state invariant.

**Retention (`R`)** — Exponential memory score used to filter active episodes.

**Saga** — Long-lived logical transaction composed of local steps with compensations.

**Saga coordinator** — Component owning step order, verification, approval, journaling, execution, compensation, and recovery decisions.

**Semantic memory** — Derived concept relationships produced from repeated episodic patterns and stored in the graph.

**Sleep cycle** — Project term for offline/periodic memory consolidation; no biological equivalence is claimed.

**SMT** — Satisfiability Modulo Theories, logic solving across types such as reals, booleans, and strings.

**SMT-LIB2** — Standard textual language used for submitted action invariants.

**Speculative execution** — Here, concurrent side-effect-free candidate pre-validation followed by real execution of the first committable candidate. It is not copy-on-write branching.

**Step** — One action, compensation, invariant, identity, approval requirement, and status inside a Saga.

**Tenant** — Logical customer/security partition used in REST authorization and memory/Saga records.

**TimescaleDB** — PostgreSQL extension used for time-oriented episodic storage; pgvector provides vector operations.

**Tool** — Registered executable capability with metadata, policy, and optional compensation handler.

**Typed DSL** — Small data structure language for compensation contracts; it avoids arbitrary callbacks so verification semantics remain auditable.

**Vacuous proof** — A proof with no admissible starting case because the precondition is never true. SagaMind rejects it.

**WASI** — WebAssembly System Interface. SagaMind uses Wasmtime's WASI support as the execution path for untrusted compiled tools.

**Write-ahead intent** — Persisting the action and inverse before external execution.

**Z3** — SMT solver used to refute violations of supplied concrete-action invariants.

