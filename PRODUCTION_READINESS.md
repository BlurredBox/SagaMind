# Production-readiness evidence

Status: **release candidate for the bounded reference deployment**, not a guarantee for
arbitrary tools or an independently validated scientific discovery.

## Enforced release gates

- Production configuration requires authentication, non-default database secrets,
  `REQUIRE_BACKENDS=true`, PostgreSQL Saga state, isolated workers, and no host fallback.
- `/health` is liveness; `/ready` returns 503 if required production dependencies or
  execution boundaries are unavailable.
- Mutating built-ins require typed policies and capability manifests. Unknown tools and
  the removed generic SQL stub fail closed.
- File writes use exact text preimages, optimistic hash/absence preconditions, atomic
  replacement, and `RESTORE_FILE` rollback.
- The write-ahead effect journal records inverse intent before execution and uses
  compare-and-set transitions. Ambiguous outcomes and failed compensations are
  dead-lettered instead of guessed.
- Approval-pending work is persisted and reconstructed after coordinator restart.
- Speculative candidates perform side-effect-free real-policy preflight; only the winner
  enters the normal journaled Saga path.
- Memory ingestion, access reinforcement, tenant enumeration, and scheduled per-tenant
  consolidation have public runtime paths.
- Tenant-bound API keys constrain Saga access, memory operations, and dead-letter reads.
- CI gates lint, formatting, type checks, unit/property tests, critical coverage, live
  backend integration, gRPC code generation, image build, package build/install,
  dependency/secret scans, and the frozen public-baseline experiment.

## Verified evidence on 24 September 2026

- Unit/property suite: 274 passing and one live-backend integration collection skipped locally.
- Source coverage: 75.98%; every designated critical module exceeds the 85% floor.
- Static analysis: Ruff and mypy pass across `src`, `sdk`, and `external_validation`.
- Package: sdist and wheel build successfully, pass `twine check`, and import from
  clean base and gRPC-extra environments; CI enforces the same base-package gate.
- Public-baseline v2: 1,000 raw trials and a separate standard-library recalculation.
  Integrated SagaMind and both matched-control public configurations achieved safe
  outcomes on 200/200 cases. Native configurations are reported separately; this is not
  a general framework leaderboard.
- Reproducibility: exact hashed Python 3.11 lock, frozen protocol/corpus, source/result
  digests, input and complete-bundle manifests, and an attestation template.

## Deployment and rollback

1. Back up PostgreSQL/TimescaleDB, Neo4j, and Redis and record the image digest.
2. Apply numbered migrations in order and run the live-backend integration suite.
3. Start one canary with production settings and wait for `/ready` to return 200.
4. Exercise authenticated Saga start, exact file rollback, approval/restart recovery,
   memory ingest/retrieve, metrics, and dead-letter tenant isolation.
5. Expand traffic while watching compensation failures, dead letters, latency, backend
   readiness, and request-correlated logs/traces.
6. Roll back on readiness failure, unexpected ambiguous effects, migration errors, or a
   sustained compensation-failure increase. Stop new writes, restore the prior image,
   run recovery once against the durable journal, and resolve dead letters before
   reopening traffic. Database rollback must use the migration-specific reverse plan or
   a tested backup restore; never discard the effect journal.

## Boundaries that remain external or deliberately unsupported

- Independent replication requires an unaffiliated executor and signed attestation.
- “Best in market” and scientific novelty require broader real-world benchmarks,
  independent review, and publication; repository tests cannot prove those claims.
- Docker/live-service CI and Linux resource-limit behavior are configured but cannot be
  executed on this Mac host because no Docker daemon is available here.
- The optional Temporal module is not the default REST execution path and its coarse
  whole-Saga activity is unsuitable for non-idempotent effects without stronger
  per-step modeling and external effect resolution.
- gRPC now has committed bindings, durable state, tenant/API-key checks, and production
  TLS enforcement, but its string-only protobuf argument map is not full REST schema
  parity. Keep it private unless the deployed tool set fits that contract.
- The built-in file contract is for contained UTF-8 text files up to 1 MB. Arbitrary
  databases, cloud APIs, binary files, permissions/ACLs, and irreversible side effects
  require purpose-built tools and evidence.
