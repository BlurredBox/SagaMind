"""
SagaMind Configuration
======================

Typed, environment-driven settings backed by ``pydantic-settings``.

Design principles
-----------------
* **Single source of truth** — every subsystem imports the ``settings`` singleton.
* **Fail closed in production** — the process refuses to boot if required secrets are
  missing or still set to their development defaults, instead of silently running
  insecurely (the previous behaviour only emitted a warning).
* **Safe defaults in development** — local development and the test-suite work with no
  configuration at all.

Configuration is read from (in order of precedence): explicit constructor kwargs →
environment variables → a ``.env`` file → the field defaults below.
"""

from __future__ import annotations

import os

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder credentials that ship in the repo for local development only.
# Their presence in a *production* environment is treated as a fatal misconfiguration.
_DEV_DEFAULT_SECRETS: frozenset[str] = frozenset(
    {
        "",
        "changeme",
        "your-sk-key-here",
        "sagamind_secure_pass_2026",
        "sagamind_secure_neo_2026",
    }
)


class Settings(BaseSettings):
    """Central application settings. Instantiate once as the module-level ``settings``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Server ──────────────────────────────────────────────────────────
    env: str = "development"
    host: str = "0.0.0.0"  # noqa: S104 - bind-all is intentional for containerised deploys
    port: int = 8000
    grpc_port: int = 50051
    grpc_tls_cert: str = ""
    grpc_tls_key: str = ""

    # ── Security / API ─────────────────────────────────────────────────
    # Comma-separated list of accepted API keys. When empty, authentication is
    # DISABLED (development convenience). Production requires at least one key.
    api_keys: str = ""
    # Comma-separated CORS allow-list. Empty => no cross-origin browser access.
    cors_origins: str = ""
    # Per-key fixed-window rate limit (requests/minute). 0 disables rate limiting.
    rate_limit_per_minute: int = 0
    # Maximum accepted request body size in bytes (default 1 MiB).
    max_request_bytes: int = 1_048_576
    # Filesystem jail root for all sandboxed writes. Defaults to the current
    # working directory so the test-suite and local runs are self-contained.
    allowed_workspace_root: str = Field(default_factory=os.getcwd)

    # Built-in tools execute in a short-lived worker process by default.  The
    # worker is a crash/resource boundary around trusted built-ins; it is not a
    # container or microVM.  ``host`` mode exists only for deliberate local
    # debugging and is rejected in production.
    sandbox_execution_mode: str = "isolated"
    sandbox_allow_host_fallback: bool = False
    sandbox_worker_timeout_s: float = 10.0
    sandbox_memory_limit_mb: int = 256
    sandbox_fuel_limit: int = 1_000_000
    sandbox_max_output_bytes: int = 65_536

    # When true, a failed backend connection raises instead of silently falling
    # back to the in-memory emulators. Recommended in production.
    require_backends: bool = False

    # ── TimescaleDB ─────────────────────────────────────────────────────
    db_host: str = "127.0.0.1"
    db_port: int = 5432
    db_name: str = "sagamind"
    db_user: str = "sagamind_user"
    db_pass: str = "sagamind_secure_pass_2026"

    # ── Neo4j ───────────────────────────────────────────────────────────
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_pass: str = "sagamind_secure_neo_2026"
    neo4j_timeout_s: int = 30

    # ── Redis ───────────────────────────────────────────────────────────
    redis_host: str = "127.0.0.1"
    redis_port: int = 6379

    # Force a specific saga-state backend ("memory" | "redis" | "postgres").
    # Empty = auto-detect (Postgres → Redis → memory). Tests pin this to "memory".
    state_store_backend: str = ""

    # ── Engine / LLM ────────────────────────────────────────────────────
    z3_path: str = "z3"
    z3_timeout_ms: int = 5000
    openai_api_key: str | None = None
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536
    consolidation_model: str = "gpt-4"
    # Cron expression for automatic sleep-cycle consolidation. Empty = disabled.
    consolidation_cron: str = ""

    # ── pgvector HNSW tuning (§4.6) ─────────────────────────────────────
    # Index build params: higher m/ef_construction = better recall, slower build.
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64
    # Per-session query-time search width: higher = better recall, slower query.
    hnsw_ef_search: int = 40

    # ── Distributed orchestration (§6.3) ───────────────────────────────
    # Temporal server target. Empty = disabled (single-process saga execution).
    temporal_target: str = ""
    temporal_namespace: str = "default"
    temporal_task_queue: str = "sagamind-sagas"

    # ── Derived helpers ─────────────────────────────────────────────────
    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """PostgreSQL/TimescaleDB connection URL."""
        return f"postgresql://{self.db_user}:{self.db_pass}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def is_production(self) -> bool:
        return self.env.lower() == "production"

    @property
    def api_key_set(self) -> set[str]:
        """Parsed set of accepted API keys (empty => auth disabled)."""
        return set(self.api_key_tenant_map)

    @property
    def api_key_tenant_map(self) -> dict[str, str | None]:
        """Parsed API keys, optionally bound to a tenant via ``key:tenant_id``.

        A key with no ``:tenant_id`` suffix is unrestricted (usable for any tenant),
        preserving backward compatibility with plain comma-separated key lists.
        """
        out: dict[str, str | None] = {}
        for raw in self.api_keys.split(","):
            entry = raw.strip()
            if not entry:
                continue
            if ":" in entry:
                key, tenant_id = entry.split(":", 1)
                out[key.strip()] = tenant_id.strip() or None
            else:
                out[entry] = None
        return out

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def auth_enabled(self) -> bool:
        """Auth is enforced when keys are configured or we are in production."""
        return bool(self.api_key_set) or self.is_production

    def model_post_init(self, __context: object) -> None:
        """Validate critical invariants once construction completes."""
        self._validate_workspace_root()
        self._validate_sandbox()
        if self.is_production:
            self._validate_production()

    def _validate_workspace_root(self) -> None:
        root = self.allowed_workspace_root
        if not os.path.isabs(root):
            raise ValueError(f"ALLOWED_WORKSPACE_ROOT must be an absolute path, got: {root!r}")

    def _validate_sandbox(self) -> None:
        if self.sandbox_execution_mode not in {"isolated", "host"}:
            raise ValueError("SANDBOX_EXECUTION_MODE must be 'isolated' or 'host'.")
        if self.sandbox_worker_timeout_s <= 0:
            raise ValueError("SANDBOX_WORKER_TIMEOUT_S must be positive.")
        if self.sandbox_memory_limit_mb < 64:
            raise ValueError("SANDBOX_MEMORY_LIMIT_MB must be at least 64.")
        if self.sandbox_fuel_limit <= 0:
            raise ValueError("SANDBOX_FUEL_LIMIT must be positive.")
        if self.sandbox_max_output_bytes < 1_024:
            raise ValueError("SANDBOX_MAX_OUTPUT_BYTES must be at least 1024.")

    def _validate_production(self) -> None:
        """Refuse to boot a production process with insecure configuration."""
        problems: list[str] = []
        if self.db_pass in _DEV_DEFAULT_SECRETS:
            problems.append("DB_PASS is unset or using a known development default.")
        if self.neo4j_pass in _DEV_DEFAULT_SECRETS:
            problems.append("NEO4J_PASS is unset or using a known development default.")
        if not self.api_key_set:
            problems.append("API_KEYS must define at least one key in production.")
        if self.sandbox_execution_mode != "isolated":
            problems.append("SANDBOX_EXECUTION_MODE must be 'isolated' in production.")
        if self.sandbox_allow_host_fallback:
            problems.append("SANDBOX_ALLOW_HOST_FALLBACK must be false in production.")
        if not self.require_backends:
            problems.append("REQUIRE_BACKENDS must be true in production.")
        if self.state_store_backend != "postgres":
            problems.append("STATE_STORE_BACKEND must be 'postgres' in production.")
        if bool(self.grpc_tls_cert) != bool(self.grpc_tls_key):
            problems.append("GRPC_TLS_CERT and GRPC_TLS_KEY must be configured together.")
        if problems:
            raise RuntimeError(
                "Refusing to start in production with insecure configuration:\n  - "
                + "\n  - ".join(problems)
                + "\nSet the corresponding environment variables / secrets and retry."
            )


settings = Settings()
