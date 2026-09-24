# Contributing to SagaMind

Thanks for your interest in improving SagaMind. This guide covers local setup, the quality
gates, and the contribution workflow.

## Development setup

```bash
git clone https://github.com/Harut200/SagaMind.git
cd SagaMind
python -m venv .venv && source .venv/bin/activate
make dev            # installs dev + dashboard + wasm extras
pre-commit install  # enable the local hooks
cp .env.example .env
```

The codebase degrades gracefully when optional backends (TimescaleDB, Neo4j, wasmtime, Z3,
OpenAI) are absent, so the full test-suite runs with no external services.

## Quality gates

Every change must pass the same checks CI enforces:

```bash
make lint     # ruff check
make format   # ruff format + autofix
make type     # mypy src
make cover    # whole source >=70%; each safety-critical target >=85%
```

* **Style** — `ruff` (lint + format). Line length 120. Keep imports sorted.
* **Types** — public functions should be typed; `mypy src` must pass.
* **Tests** — add or update tests next to the subsystem you touch. Mirror the existing
  mock-based style in `tests/`; if you add a new module-level singleton to `src/main.py`,
  patch it in `tests/test_api.py`.
* **Security** — never commit secrets. `gitleaks` runs in pre-commit and CI. Keep the
  `_DEV_DEFAULT_SECRETS` guard in `src/config.py` honest.

## Architectural conventions

The production roadmap and rationale live in [improve.md](improve.md).

## Pull requests

1. Branch from `main`; keep PRs focused.
2. Describe the change, its motivation, and how you tested it.
3. Ensure CI is green. Update `CHANGELOG.md` under "Unreleased".
4. Do not introduce phantom surface area — wire new capabilities end-to-end or gate them
   clearly behind a flag and document the gap.

## Commit messages

Use conventional, imperative summaries (`fix:`, `feat:`, `docs:`, `refactor:`, `test:`).
