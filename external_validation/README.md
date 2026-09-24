# External validation and independent-replication package

This directory provides a frozen, framework-neutral workload for comparing SagaMind
with native and matched-control public configurations:

- Temporal 1.33.0 using its documented reverse-order Saga compensation pattern, with
  and without an application path guard;
- LangGraph 1.2.12 using its documented `InMemorySaver` checkpoint pattern, with
  and without application path validation plus preimage rollback.

The comparison is deliberately narrow. Native configurations show what the named
patterns provide by themselves; matched variants demonstrate what happens when the
missing application controls are added. The result compares configurations and
capabilities, not overall framework quality.

## Frozen method

`protocol_v2.json` fixes the seed, sample size, systems, endpoints, scoring rules, and
statistics. It was frozen locally before the first baseline run but was not registered
with an external preregistration service. `trial_corpus.jsonl` contains opaque case identifiers and operations in a
deterministically shuffled order. Adapters never receive expected labels. The common
runner creates a fresh temporary filesystem for every system/case pair, and the scorer
inspects the bytes on disk rather than trusting framework return values.

There are 100 partial-mutation/runtime-failure cases and 100 workspace-escape cases per
system. Rates use Wilson 95% intervals; paired differences use exact McNemar tests.
Latency is descriptive only because the configurations provide different services.

## Third-party execution

From a clean checkout on Python 3.11:

```bash
./external_validation/run_replication.sh
```

The script installs the combined exact runtime/public-baseline lock,
runs all 1,000 system/case pairs, and recalculates the primary metric with a separate
standard-library verifier. Network access is required on the first run because Temporal's
test environment downloads its official ephemeral server.

Copy `ATTESTATION.template.json`, fill it after execution, attach the generated
`external_validation/results/summary.json` and `trials.csv`, and sign the combined result
hash printed by `verify_results.py`. An author-run or AI-run execution is a self-check,
not independent replication. Only an unaffiliated executor may set
`independent_replication` to `true`.

The runner verifies `SOURCE_MANIFEST.sha256` before installing dependencies. After a
run, `MANIFEST.sha256` can verify the complete distributed bundle, including results.

## Primary sources

- Temporal Python error-handling guidance (Saga compensations):
  <https://docs.temporal.io/develop/python/best-practices/error-handling>
- Temporal Python SDK and test environment:
  <https://github.com/temporalio/sdk-python>
- LangGraph persistence semantics:
  <https://docs.langchain.com/oss/python/langgraph/persistence>
