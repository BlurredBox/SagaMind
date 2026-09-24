# Reproducible evaluation

Run the complete offline evaluation from the repository root:

```bash
python -m experiments.evaluate
```

The command writes machine-readable results to `experiments/results/summary.json`,
paired fault-injection observations to `experiments/results/saga_trials.csv`, and every
real-filesystem ablation observation to `experiments/results/filesystem_ablation_trials.csv`.
A fixed seed, Git revision, Python version, and platform are recorded with each run.

The suite evaluates four narrow implementation hypotheses:

1. LIFO compensation restores a controlled state after injected failures, compared with
   naive sequential execution without compensation.
2. The SMT gate agrees with an independent oracle on generated arithmetic policies.
3. Cosine-DBSCAN recovers known groups in a seeded synthetic dataset.
4. The retention function has its claimed monotonic properties.
5. Verification and compensation address different filesystem failure classes; four
   variants (sequential, verify-only, Saga-only, and full) expose their individual and
   combined effect. Reported outcomes include confidence intervals, residual effects,
   unsafe executions, solver errors, and latency.
6. Deterministic semantic checks cover false tool results, duplicate idempotency keys,
   an ambiguous crash boundary, and compensation failure escalation.

This is component evidence, not an LLM-agent benchmark. It does not establish better task
success, human-like memory, production fault tolerance, or novelty over all prior systems.
Those claims require preregistered external benchmarks and independent replication.
