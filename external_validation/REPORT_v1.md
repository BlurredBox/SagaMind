# Public-baseline comparison and replication status

Generated from `200` identical cases per system on Python 3.11.1.

| Configuration | Trials | Safe outcome | Runtime recovery | Unsafe-path block | Residual effects | Unsafe executions |
|---|---:|---:|---:|---:|---:|---:|
| SagaMind full | 200 | 1.000 | 1.000 | 1.000 | 0 | 0 |
| Temporal 1.33.0 Saga | 200 | 0.500 | 1.000 | 0.000 | 0 | 100 |
| LangGraph 1.2.12 checkpoint | 200 | 0.000 | 0.000 | 0.000 | 200 | 100 |

SagaMind was safe on 200/200 cases (Wilson 95% CI [0.9812, 1.0000]). Temporal's documented Saga configuration restored all 100 injected runtime failures but did not block the 100 unsafe paths because no application path policy was configured. LangGraph's documented checkpoint configuration preserved graph state but did not undo or policy-check external filesystem writes.

The paired exact McNemar p-values versus SagaMind were 1.578e-30 for Temporal and 1.245e-60 for LangGraph. These tests describe this fixed corpus; they do not prove superiority over arbitrary workloads.

## Reproducibility record

- Repository revision: `06ef2b1cb4e77e0bebdba2ba3209ffa157d88085`; dirty worktree recorded as `true`.
- Evaluated-source digest: `9d814528cd9fa5998df78b90bdd324274fc5b54a88436a6e6a159d3720489150`.
- Protocol digest: `c97274ef6ecad33b0dfaa631a091c437b60de3f212ff2778340f40dac5c30575`.
- Corpus file digest: `a68da322d9a4a76aad9426a82eab8046c22c14ed3ba8135aaa9c601897cdd37e`.
- Combined dependency-lock digest: `9777c0a57175d7213c90dea9ee765a32e5d9203c553bcaa517d7d9edf2c2786e`.
- Raw rows: `external_validation/results/trials.csv` (600 observations).
- Independent recalculation: all rates match and all systems used identical case identifiers.

## Interpretation limits

- This is a capability/configuration comparison, not an overall framework leaderboard.
- Temporal and LangGraph can be extended with application validation and compensation. Those extensions were not attributed to the frameworks' native configurations here.
- Temporal used its official ephemeral test server; this run did not test production clusters, worker restarts, network partitions, or Activity ambiguity.
- LangGraph used `InMemorySaver`, exactly as the documented quickstart; the documentation states that it does not persist across process restarts.
- Latencies are descriptive and not suitable for ranking because the systems provide different services and Temporal crosses a local workflow-service boundary.
- The author/AI-run result is a validated self-replication, not independent replication. The independent claim remains false until an unaffiliated executor runs the frozen bundle and signs `ATTESTATION.template.json`.

## Primary sources

- [Temporal Python Saga guidance](https://docs.temporal.io/develop/python/best-practices/error-handling)
- [Temporal Python testing guidance](https://docs.temporal.io/develop/python/best-practices/testing-suite)
- [LangGraph persistence documentation](https://docs.langchain.com/oss/python/langgraph/persistence)
