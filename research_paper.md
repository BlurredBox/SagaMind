# SagaMind: A Reproducible Systems Study of Compensating Transactions, SMT Policy Gates, and Tiered Agent Memory

**Artifact status:** research prototype and controlled component evaluation
**Author:** Harutyun Kesablyan
**Evaluation date:** 24 September 2026
**Protocol seed:** `20260924`

## Abstract

Tool-using language-model agents can mutate external state, yet their plans are stochastic and may fail after partial execution. SagaMind studies a bounded systems question: can established software mechanisms—Saga compensations, satisfiability-modulo-theories (SMT) checks, explicit memory decay, and density-based clustering—be composed into one agent runtime with testable failure semantics?

We implement a Python prototype and an offline, seeded evaluation. In 1,000 paired synthetic fault-injection trials, registered LIFO compensations restored the controlled state in 1,000 trials (rate 1.000; Wilson 95% CI [0.9962, 1.0000]), while naive sequential execution restored none (0.000; 95% CI [0.0000, 0.0038]); exact McNemar p < 1.9×10⁻³⁰¹. On 1,000 generated concrete arithmetic policies, the Z3-backed gate agreed with an independent oracle in all cases (95% CI [0.9962, 1.0000]), with sub-millisecond p95 latency on the recorded test machine. In 800 real temporary-filesystem trials, the unprotected baseline produced no safe outcomes, verifier-only and Saga-only each achieved 0.500 by addressing different failure classes, and the combined system achieved 1.000 (95% CI [0.9812, 1.0000]). Across 30 seeded synthetic clustering datasets, deterministic cosine-DBSCAN obtained mean pairwise F1 1.000 for four deliberately separated groups. A 360-case grid found no violations of the decay function's stated monotonic properties.

These results validate narrow implementation hypotheses under controlled conditions. A frozen extension also compares identical filesystem cases against Temporal 1.33.0's documented Saga pattern and LangGraph 1.2.12's documented checkpoint pattern. It does not establish better end-to-end agent task performance, human-like memory, general distributed consistency, or scientific novelty of the constituent algorithms. SagaMind is therefore best described as a reproducible systems artifact and research candidate—not a confirmed scientific discovery. Broader benchmarks, realistic failures, and unaffiliated replication are required before stronger claims are justified.

## 1. Research question and contribution

Primary question:

> Can an agent runtime make state mutation auditable and recoverable by placing explicit policy verification before each action and registered compensations after partial failure?

SagaMind's contribution is implementation and evaluation of a composition:

1. a write-ahead effect journal with explicit ambiguous-outcome recovery;
2. typed mutation policies plus a concrete-argument SMT escape hatch that fails closed;
3. bounded compensation-restoration contracts with implementation-bound certificates;
4. capability-scoped isolated workers and an optional WASI execution primitive;
5. a tiered memory pipeline with parameterized decay and deterministic cosine-DBSCAN; and
6. a reproducible evaluation command that emits raw observations and machine-readable summaries.

We do **not** claim invention of Sagas, SMT solving, DBSCAN, exponential decay, or complementary learning systems. The Saga pattern originates with García-Molina and Salem (1987); DBSCAN with Ester et al. (1996); Z3 with de Moura and Bjørner (2008); and the biological motivation for complementary learning systems with McClelland, McNaughton, and O'Reilly (1995).

## 2. System model

### 2.1 Compensating transaction execution

A workflow contains ordered pairs

$$
W = \langle (T_1,C_1), (T_2,C_2), \ldots, (T_n,C_n) \rangle,
$$

where $T_i$ is a forward action and $C_i$ is its application-defined compensation. If $T_k$ fails, the coordinator invokes $C_{k-1},\ldots,C_1$ in reverse order. This is backward recovery, not database isolation: concurrent observers may see intermediate states, compensations may be approximate, and external side effects may be irreversible.

Before invoking an external effect, the runtime persists both the forward action and its inverse. The durable lifecycle is `PREPARED → EXECUTING → APPLIED → COMMITTED`; compensation has separate states. A crash during an external call creates an ambiguous outcome that must be resolved through an observer/idempotent adapter or escalated. The runtime records a distinct terminal state when compensation fails and sends that case to a dead-letter queue.

### 2.2 SMT policy gate

For concrete action arguments $A$ and caller-supplied invariant $I$, the verifier asks whether

$$
A \land \neg I
$$

is satisfiable. `unsat` means no counterexample exists for those concrete bindings and the action passes that invariant. `sat` yields a counterexample and rejects the action. `unknown` and timeout reject the action.

Built-in mutations additionally require typed policies covering required fields, scalar types, bounds, enumerations, maximum lengths, extra arguments, and canonical path containment. Missing policy and unsupported/nested values fail closed. Raw SMT remains an advanced compatibility path. This is still a conditional guarantee: the checker cannot establish that a policy captures human intent.

### 2.3 Compensation contracts

The contract DSL declares finite typed state/input domains, a precondition, forward transition, postcondition, compensation, and restoration invariant. Exhaustive bounded verification returns `PROVED`, `REJECTED`, `INVALID`, `UNKNOWN`, or `UNSUPPORTED`; vacuous, unbounded, excessive, and irreversible models cannot receive a proof. The certificate hashes the complete contract, proof result, and declared tool implementation identity. Registration can require that a proved certificate matches the implementation identity. This binds evidence to an identity; it does not prove that external code implements the model.

### 2.4 Execution boundary

Bundled tools execute in short-lived worker processes after typed-policy and capability checks. Capabilities cover filesystem access, network declarations, environment variables, CPU/time, memory, fuel, and output size. Production rejects host fallback or unavailable workers. The worker boundary contains trusted reference handlers but is not a hostile-code container or microVM; untrusted compiled tools require the WASI path or an external sandbox.

### 2.5 Memory policy

For elapsed time $\Delta t$ in hours, importance $I_m$, and retrieval count $N_m$, implemented retention score is

$$
R_m(\Delta t) = \exp\left(-\frac{\Delta t}{S_m}\right), \qquad
S_m = S_0\left(1 + \gamma\ln(N_m+1)\right)I_m.
$$

$S_0$ is an exponential time constant, **not** a half-life. Corresponding half-life is $S_m\ln 2$. The score is an engineering priority heuristic inspired by forgetting curves; it is not fitted to human-subject data and must not be interpreted as a cognitive model validated by this study.

Consolidation applies deterministic DBSCAN with cosine distance. Points labeled as noise create no concept node. Optional language-model summarization can label a cluster, but it is excluded from the offline experiment so model variance and network access cannot affect reproduction.

## 3. Hypotheses

- **H1 — recovery:** after one injected forward failure, registered compensations restore the controlled mutable state more often than naive sequential execution.
- **H2 — policy classification:** for generated concrete arithmetic inputs, the SMT gate agrees with a direct Boolean oracle.
- **H3 — clustering:** deterministic cosine-DBSCAN recovers deliberately separated synthetic concept groups.
- **H4 — decay properties:** retention is non-increasing in elapsed time and non-decreasing in retrieval count over the tested parameter grid.
- **H5 — control composition:** verification blocks unsafe paths, compensation restores runtime failures, and their combination covers both classes.

H1, H2, and H5 test runtime behavior. H3 is a pipeline sanity test, not evidence of real-memory quality. H4 checks algebraic behavior, not downstream utility.

## 4. Method

### 4.1 Reproducibility

Run:

```bash
python -m experiments.evaluate
```

Command records seed, Git revision, dirty-tree flag, UTC timestamp, Python version, platform, aggregate statistics, and raw paired Saga observations. Default artifacts:

- `experiments/results/summary.json`
- `experiments/results/saga_trials.csv`
- `experiments/results/filesystem_ablation_trials.csv`

No API key, hosted model, database, or network service is used.

### 4.2 Fault injection

Each of 1,000 trials samples workflow length uniformly from 2 through 12 and a failure position from 1 through $n-1$. Pre-failure actions append typed mutations to controlled state. Each compensation must remove most recent matching mutation, so test detects incorrect order. Paired baseline runs same forward mutations without compensation.

Primary outcome: exact restoration to initial empty state. We report Wilson score intervals for each rate and a two-sided exact McNemar test for paired binary outcomes.

### 4.3 SMT classification

For 1,000 seeded inputs, amount, balance, and privilege values are generated independently. Policy requires non-negative amount, amount not exceeding balance, and privilege for amounts over 1,000. Expected labels come from direct Boolean implementation independent of SMT parsing. We report accuracy, false accepts, false rejects, and per-case verification latency.

### 4.4 Synthetic clustering

Each of 30 datasets contains four groups of 20 points in 32 dimensions. Group centers are orthogonal basis vectors; isotropic Gaussian noise with standard deviation 0.025 is added. Cosine-DBSCAN uses $\epsilon=0.08$ and `min_samples=3`. We report pairwise F1 and discovered cluster count. Parameters are fixed before evaluation in script.

### 4.5 Decay properties

Grid crosses five importance values, six retrieval counts, and eight elapsed-time values. Separate sweeps test monotonicity in time and retrieval count with numerical tolerance $10^{-12}$.

### 4.6 Real-filesystem ablation and failure matrix

Four variants run against disposable real directories: sequential/no controls, verification only, Saga compensation only, and full. Even-numbered trials overwrite one file, create another, then raise; odd-numbered trials attempt a path escape. We report safe outcomes, restoration, unsafe execution, residual effects, Wilson intervals, and latency. Separate deterministic cases exercise a false tool result, duplicate idempotency key, crash after effect execution but before durable acknowledgement, and failed compensation escalation.

## 5. Results

<!-- GENERATED_RESULTS:START -->
| Component | Sample | Outcome |
|---|---:|---|
| Saga fault injection | 1,000 paired trials | SagaMind 1.000 restored; naive 0.000; exact McNemar p=1.87e-301 |
| SMT classification | 1,000 cases | Accuracy 1.000; 0 false accepts; 0 false rejects |
| SMT latency | 1,000 cases | Median 0.74 ms; p95 0.96 ms |
| Synthetic clustering | 30 datasets | Mean pairwise F1 1.000; mean 4 clusters |
| Decay properties | 360 checks | 0 monotonicity violations |
| Filesystem ablation: sequential | 200 trials | Safe 0.000; recovery 0.000; path block 0.000; residual effects 200; unsafe executions 100 |
| Filesystem ablation: verify-only | 200 trials | Safe 0.500; recovery 0.000; path block 1.000; residual effects 200; unsafe executions 0 |
| Filesystem ablation: Saga-only | 200 trials | Safe 0.500; recovery 1.000; path block 0.000; residual effects 0; unsafe executions 100 |
| Filesystem ablation: full | 200 trials | Safe 1.000; recovery 1.000; path block 1.000; residual effects 0; unsafe executions 0 |
| Failure-semantics matrix | 4 deterministic cases | 4 passed |
<!-- GENERATED_RESULTS:END -->

Perfect synthetic clustering result reflects intentionally separable sanity-test data. It must not be extrapolated to natural-language embeddings. Likewise, H1's effect is expected in a model where every completed action has an exact, successful inverse; important result is that implementation preserves this property under randomized workflow length and failure position.

### 5.1 Named public-baseline extension

A repository-local v2 protocol was frozen before the integrated comparison run, but was not externally preregistered. The runner supplied the same shuffled 200-case corpus to SagaMind's integrated local path and to native and matched-control Temporal 1.33.0 and LangGraph 1.2.12 configurations. Half the cases performed two real filesystem mutations and then failed; half attempted a write outside the allowed workspace. A framework-neutral scorer inspected filesystem bytes directly.

| Configuration | Trials | Safe outcome | Runtime recovery | Unsafe-path block | Residual effects | Unsafe executions |
|---|---:|---:|---:|---:|---:|---:|
| SagaMind integrated local | 200 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| Temporal Saga, native | 200 | 0.500 | 1.000 | 0.000 | 0 | 100 |
| Temporal Saga + path guard | 200 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| LangGraph checkpoint, native | 200 | 0.000 | 0.000 | 0.000 | 200 | 100 |
| LangGraph + matched controls | 200 | 1.000 | 1.000 | 1.000 | 0 | 0 |

SagaMind's primary rate was 1.000 (Wilson 95% CI [0.9812, 1.0000]). Against SagaMind, paired exact McNemar p was $1.58\times10^{-30}$ for native Temporal and $1.24\times10^{-60}$ for native LangGraph; both matched-control variants tied SagaMind at 1.000 (McNemar p=1.0). The matched results are essential: this workload does not establish general framework superiority, and both public frameworks can satisfy it with the relevant application safeguards. Temporal used its official ephemeral test server; the study did not exercise a production cluster, process crash, or network partition. Latencies are not compared inferentially because the systems expose different services. Raw rows, exact hashed locks, source/corpus hashes, and the separate metric recalculator are in `external_validation/`.

This run is a self-replication performed within the project, not independent replication. The frozen bundle and attestation are ready for an unaffiliated executor; no independent-replication claim is made until that attestation is signed.

## 6. Threats to validity

**Construct validity.** Empty-list restoration approximates consistency but excludes semantic, partially reversible, and third-party effects. Synthetic vector separation does not represent real agent memories. Arithmetic policies cover only a small part of SMT-LIB.

**Internal validity.** Implementation and oracle were written by the same project. Despite separate code paths, shared misunderstandings remain possible. Crash boundaries are injected deterministically in one process; they do not reproduce every operating-system, storage, or network failure.

**External validity.** Results come from one local ARM macOS environment. The core study exercises no live TimescaleDB, Neo4j, Redis, WASI toolchain, or multi-process production deployment. The public-baseline extension uses Temporal's ephemeral test server, not a production cluster. Latency is not portable across hardware, solver, or framework versions.

**Statistical conclusion validity.** Confidence intervals quantify repeated generated cases, not universe of real workflows. Generated cases are seeded and conditionally independent, but are not a random sample of production agent behavior.

## 7. What would justify a discovery claim

A defensible stronger claim needs, at minimum:

1. preregistered hypotheses and frozen evaluation code;
2. public, realistic agent workloads and failure traces;
3. broader public baselines beyond the included LangGraph checkpoint and Temporal Saga configurations;
4. external ablations for memory, graph consolidation, and speculative execution beyond the included verification/compensation study;
5. end-to-end measures: task success, residual corruption, recovery time, token cost, retrieval quality, and operator burden;
6. multiple machines, solver versions, model providers, and seeds;
7. adversarial tests for policy gaps and irreversible effects; and
8. unaffiliated replication using the prepared bundle, followed by peer review.

Until those steps are complete, evidence supports “tested research prototype,” not “proven scientific discovery.”

## References

1. García-Molina, H., & Salem, K. (1987). *Sagas*. Proceedings of ACM SIGMOD, 249–259. https://doi.org/10.1145/38713.38742
2. Ester, M., Kriegel, H.-P., Sander, J., & Xu, X. (1996). *A Density-Based Algorithm for Discovering Clusters in Large Spatial Databases with Noise*. KDD-96. https://aaai.org/papers/kdd96-037-a-density-based-algorithm-for-discovering-clusters-in-large-spatial-databases-with-noise/
3. de Moura, L., & Bjørner, N. (2008). *Z3: An Efficient SMT Solver*. TACAS 2008. https://www.microsoft.com/en-us/research/publication/z3-an-efficient-smt-solver/
4. McClelland, J. L., McNaughton, B. L., & O'Reilly, R. C. (1995). *Why There Are Complementary Learning Systems in the Hippocampus and Neocortex*. Psychological Review, 102(3), 419–457. https://doi.org/10.1037/0033-295X.102.3.419
5. Maharana, A., et al. (2024). *Evaluating Very Long-Term Conversational Memory of LLM Agents*. arXiv:2402.17753. https://arxiv.org/abs/2402.17753
6. Temporal Technologies. (2026). *Error handling — Python SDK: Implement rollback logic with the Saga pattern*. https://docs.temporal.io/develop/python/best-practices/error-handling
7. LangChain. (2026). *LangGraph persistence*. https://docs.langchain.com/oss/python/langgraph/persistence
