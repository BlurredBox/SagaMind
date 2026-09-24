# Public-baseline comparison and replication status

Generated from `200` identical cases per system on Python 3.11.1.

| Configuration | Trials | Safe outcome | Runtime recovery | Unsafe-path block | Residual effects | Unsafe executions |
|---|---:|---:|---:|---:|---:|---:|
| SagaMind integrated local | 200 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| Temporal 1.33.0 Saga (native) | 200 | 0.500 | 1.000 | 0.000 | 0 | 100 |
| Temporal 1.33.0 Saga + path guard | 200 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| LangGraph 1.2.12 checkpoint (native) | 200 | 0.000 | 0.000 | 0.000 | 200 | 100 |
| LangGraph 1.2.12 + matched controls | 200 | 1.000 | 1.000 | 1.000 | 0 | 0 |

SagaMind's integrated local configuration and both matched-control configurations passed all cases. Temporal's native Saga restored runtime failures but had no application path guard. LangGraph's native checkpoint configuration did not itself undo or policy-check external writes. The matched variants demonstrate that both public frameworks can satisfy this small workload when equivalent application controls are added.

Paired exact McNemar tests are recorded for every native and matched configuration. The matched comparisons are expected to tie when their controls work. These tests describe this fixed corpus; they do not prove superiority over arbitrary workloads.

## Reproducibility record

- Repository revision: `06ef2b1cb4e77e0bebdba2ba3209ffa157d88085`; dirty worktree recorded as `true`.
- Evaluated-source digest: `974a4be38fb0686cfb561128e99f81efb34d5fc39e66f9dc22c3068587a608e9`.
- Protocol digest: `ee97a9477e5320d1397befa2258dd7f6a1abab273d34c5daae7f6ff3e108a17c`.
- Corpus file digest: `a68da322d9a4a76aad9426a82eab8046c22c14ed3ba8135aaa9c601897cdd37e`.
- Combined dependency-lock digest: `8dcb412968803bba59c43e8f11e16abf9f22437ef99bdf4011e6347b9862c201`.
- Raw rows: `external_validation/results/trials.csv` (1000 observations).
- Secondary-verifier recalculation: all rates match and all systems used identical case identifiers.

## Interpretation limits

- This is a capability/configuration comparison, not an overall framework leaderboard.
- Temporal and LangGraph matched variants contain explicit application validation and/or compensation; these controls are not attributed to the frameworks' native configurations.
- Temporal used its official ephemeral test server; this run did not test production clusters, worker restarts, network partitions, or Activity ambiguity.
- LangGraph used `InMemorySaver`; neither LangGraph configuration is a process-restart test.
- Latencies are descriptive and not suitable for ranking because the systems provide different services and Temporal crosses a local workflow-service boundary.
- The author/AI-run result is a validated self-replication, not independent replication. The independent claim remains false until an unaffiliated executor runs the frozen bundle and signs `ATTESTATION.template.json`.

## Primary sources

- [Temporal Python Saga guidance](https://docs.temporal.io/develop/python/best-practices/error-handling)
- [Temporal Python testing guidance](https://docs.temporal.io/develop/python/best-practices/testing-suite)
- [LangGraph persistence documentation](https://docs.langchain.com/oss/python/langgraph/persistence)
