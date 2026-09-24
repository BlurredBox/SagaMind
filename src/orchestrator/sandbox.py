"""Capability-scoped execution for SagaMind tools.

Built-in tools execute in a short-lived Python worker by default. The worker
provides process, timeout, environment, and (on supported POSIX platforms)
resource boundaries around the trusted reference handlers. Filesystem paths
are independently canonicalised and checked against the configured workspace.

This is intentionally described as a *worker boundary*, not as a container,
copy-on-write filesystem, or hostile-code sandbox. Untrusted tool code must be
compiled to WASI and registered with a ``wasm_module_path``. Direct host
execution is available only as an explicit development fallback and is always
rejected in production.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

from src.config import settings
from src.contracts import VerificationCertificate, implementation_hash
from src.models import SandboxResult
from src.policy import ToolPolicy, ToolPolicyRegistry, builtin_policy_registry
from src.security import PathSecurityError, contain_path

logger = logging.getLogger("SagaMind.Sandbox")


class SandboxError(Exception):
    """Raised for sandbox policy violations or execution failures."""


@dataclass(frozen=True)
class FilesystemCapabilities:
    """Workspace-relative glob allow-lists. Empty means no filesystem access."""

    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()


@dataclass(frozen=True)
class CapabilityManifest:
    """Resources a tool is permitted to use.

    ``network`` contains explicitly allowed endpoints and ``environment`` names
    variables that may be copied into the worker. The reference built-ins use
    neither. Fuel is enforced by WASI; Python workers instead use a wall-clock
    timeout and OS CPU limit. Numeric limits are upper bounds and may be
    tightened by deployment configuration.
    """

    filesystem: FilesystemCapabilities = field(default_factory=FilesystemCapabilities)
    network: tuple[str, ...] = ()
    environment: tuple[str, ...] = ()
    fuel: int = 1_000_000
    memory_mb: int = 256
    output_bytes: int = 65_536

    def __post_init__(self) -> None:
        if self.fuel <= 0 or self.memory_mb <= 0 or self.output_bytes <= 0:
            raise ValueError("Capability resource limits must be positive.")
        if any("=" in name or not name for name in self.environment):
            raise ValueError("Environment capabilities must be variable names.")


@dataclass
class ToolDefinition:
    """A registered handler plus its explicit capability contract."""

    name: str
    handler: Callable[[dict[str, Any]], SandboxResult]
    compensation_handler: Callable[[dict[str, Any]], bool] | None = None
    wasm_module_path: str | None = None
    description: str = ""
    allowed_as_compensation: bool = False
    capabilities: CapabilityManifest = field(default_factory=CapabilityManifest)
    path_arguments: Mapping[str, str] = field(default_factory=dict)
    # Only bundled reference tools can opt into the worker protocol. A custom
    # Python callable is never silently treated as isolated.
    isolated_builtin: bool = False
    mutating: bool | None = None
    policy: ToolPolicy | None = None
    requires_verified_compensation: bool = False
    compensation_certificate: VerificationCertificate | None = None
    implementation_identity: str | None = None


class ToolRegistry:
    """Central allow-list for tool definitions."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self._comp_tools: set[str] = set()

    def register(self, tool: ToolDefinition, *, allow_as_compensation: bool = False) -> None:
        if tool.mutating is None:
            raise SandboxError(f"Tool '{tool.name}' must explicitly declare whether it mutates state.")
        if tool.capabilities.filesystem.write and tool.mutating is not True:
            raise SandboxError(f"Tool '{tool.name}' has write capability and must declare mutating=True.")
        if self.is_mutating(tool) and tool.policy is None:
            raise SandboxError(f"Mutating tool '{tool.name}' requires a typed policy.")
        if tool.policy is not None and tool.policy.tool_name != tool.name:
            raise SandboxError(f"Policy for '{tool.policy.tool_name}' cannot be attached to tool '{tool.name}'.")
        if tool.requires_verified_compensation:
            certificate = tool.compensation_certificate
            if certificate is None or not certificate.proved:
                raise SandboxError(f"Tool '{tool.name}' requires a proved compensation certificate.")
            if not tool.implementation_identity:
                raise SandboxError(f"Tool '{tool.name}' requires an implementation identity.")
            if certificate.implementation_hash != implementation_hash(tool.implementation_identity):
                raise SandboxError(f"Tool '{tool.name}' compensation certificate is for another implementation.")
        self._tools[tool.name] = tool
        if allow_as_compensation:
            self._comp_tools.add(tool.name)

    def unregister(self, name: str) -> None:
        """Remove a definition (primarily useful for isolated tests/plugins)."""
        self._tools.pop(name, None)
        self._comp_tools.discard(name)

    def get(self, name: str) -> ToolDefinition:
        if name not in self._tools:
            raise SandboxError(f"Tool '{name}' is not registered. Allowed: {sorted(self._tools)}")
        return self._tools[name]

    def is_compensation_allowed(self, name: str) -> bool:
        return name in self._comp_tools

    @staticmethod
    def is_mutating(tool: ToolDefinition) -> bool:
        return tool.mutating is True

    @property
    def allowed_actions(self) -> frozenset[str]:
        return frozenset(self._tools)

    @property
    def allowed_compensations(self) -> frozenset[str]:
        return frozenset(self._comp_tools)


registry = ToolRegistry()
_builtin_policies = builtin_policy_registry()


def _handle_write_file(args: dict[str, Any]) -> SandboxResult:
    raw_path = args.get("path")
    content = args.get("content", "")
    if not raw_path:
        raise SandboxError("WRITE_FILE requires a 'path' argument.")
    if not isinstance(content, str):
        raise SandboxError("WRITE_FILE 'content' must be a string.")
    try:
        safe_path = contain_path(str(raw_path))
    except PathSecurityError as exc:
        raise SandboxError(str(exc)) from exc
    temporary: str | None = None
    try:
        os.makedirs(os.path.dirname(safe_path) or ".", exist_ok=True)
        _check_file_precondition(safe_path, args)
        fd, temporary = tempfile.mkstemp(prefix=".sagamind-", dir=os.path.dirname(safe_path) or ".")
        with os.fdopen(fd, "w", encoding="utf-8") as file_obj:
            file_obj.write(content)
            file_obj.flush()
            os.fsync(file_obj.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, safe_path)
        temporary = None
        return SandboxResult(success=True, data={"written_path": safe_path, "bytes": len(content)})
    except OSError as exc:
        raise SandboxError(f"Failed to write file: {exc}") from exc
    finally:
        if temporary is not None:
            with suppress(FileNotFoundError):
                os.unlink(temporary)


def _file_sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_file_precondition(path: str, args: dict[str, Any]) -> None:
    expected_hash = args.get("expected_sha256")
    expected_absent = args.get("expected_absent")
    if expected_hash is not None and expected_absent is not None:
        raise SandboxError("Only one file precondition may be supplied.")
    exists = os.path.isfile(path)
    if expected_absent is True and exists:
        raise SandboxError("File precondition failed: expected path to be absent.")
    if expected_hash is not None and (not exists or _file_sha256(path) != expected_hash):
        raise SandboxError("File precondition failed: content hash changed.")


def _handle_delete_file(args: dict[str, Any]) -> SandboxResult:
    raw_path = args.get("path")
    if not raw_path:
        return SandboxResult(success=True)
    try:
        safe_path = contain_path(str(raw_path))
    except PathSecurityError as exc:
        raise SandboxError(str(exc)) from exc
    _check_file_precondition(safe_path, args)
    if not os.path.exists(safe_path):
        return SandboxResult(success=True)
    try:
        os.remove(safe_path)
        return SandboxResult(success=True, data={"deleted_path": safe_path})
    except OSError as exc:
        raise SandboxError(f"Failed to delete file: {exc}") from exc


def _comp_delete_file(args: dict[str, Any]) -> bool:
    raw_path = args.get("path")
    if not raw_path:
        return True
    try:
        safe_path = contain_path(str(raw_path))
    except PathSecurityError as exc:
        logger.error("Compensation path rejected: %s", exc)
        return False
    if not os.path.exists(safe_path):
        return True
    try:
        os.remove(safe_path)
        return True
    except OSError as exc:
        logger.error("Failed to delete file during compensation: %s", exc)
        return False


def _handle_restore_file(args: dict[str, Any]) -> SandboxResult:
    """Restore a captured file preimage or remove a file created by the forward step."""
    raw_path = args.get("path")
    try:
        safe_path = contain_path(str(raw_path))
    except PathSecurityError as exc:
        raise SandboxError(str(exc)) from exc
    _check_file_precondition(safe_path, args)
    if bool(args.get("existed")):
        result = _handle_write_file({"path": args.get("path"), "content": args.get("previous")})
        result.data["restored"] = True
        return result
    result = _handle_delete_file({"path": args.get("path")})
    result.data["restored"] = True
    return result


def _comp_restore_file(args: dict[str, Any]) -> bool:
    try:
        return _handle_restore_file(args).success
    except SandboxError as exc:
        logger.error("Failed to restore file during compensation: %s", exc)
        return False


def _handle_noop(_args: dict[str, Any]) -> SandboxResult:
    return SandboxResult(success=True)


def _comp_noop(_args: dict[str, Any]) -> bool:
    return True


_NO_ACCESS = CapabilityManifest()
_WORKSPACE_WRITE = CapabilityManifest(filesystem=FilesystemCapabilities(write=("**",)))

registry.register(
    ToolDefinition(
        "WRITE_FILE",
        _handle_write_file,
        description="Write content to a capability-scoped workspace path",
        capabilities=_WORKSPACE_WRITE,
        path_arguments={"path": "write"},
        isolated_builtin=True,
        mutating=True,
        policy=_builtin_policies.get("WRITE_FILE"),
    )
)
registry.register(
    ToolDefinition(
        "DELETE_FILE",
        _handle_delete_file,
        compensation_handler=_comp_delete_file,
        description="Delete a capability-scoped workspace file",
        allowed_as_compensation=True,
        capabilities=_WORKSPACE_WRITE,
        path_arguments={"path": "write"},
        isolated_builtin=True,
        mutating=True,
        policy=_builtin_policies.get("DELETE_FILE"),
    ),
    allow_as_compensation=True,
)
registry.register(
    ToolDefinition(
        "RESTORE_FILE",
        _handle_restore_file,
        compensation_handler=_comp_restore_file,
        description="Restore a captured workspace file preimage",
        allowed_as_compensation=True,
        capabilities=_WORKSPACE_WRITE,
        path_arguments={"path": "write"},
        isolated_builtin=True,
        mutating=True,
        policy=_builtin_policies.get("RESTORE_FILE"),
    ),
    allow_as_compensation=True,
)
registry.register(
    ToolDefinition(
        "NOOP",
        _handle_noop,
        compensation_handler=_comp_noop,
        description="Side-effect-free no-op for workflow coordination",
        allowed_as_compensation=True,
        capabilities=_NO_ACCESS,
        isolated_builtin=True,
        mutating=False,
    ),
    allow_as_compensation=True,
)


def _path_matches(relative_path: str, patterns: tuple[str, ...]) -> bool:
    path = PurePosixPath(relative_path).as_posix()
    return any(pattern == "**" or fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


class WasmSandbox:
    """Capability gate with isolated built-in workers and optional WASI tools."""

    def __init__(
        self,
        memory_limit_mb: int | None = None,
        fuel_limit: int | None = None,
        *,
        execution_mode: str | None = None,
        allow_host_fallback: bool | None = None,
        max_output_bytes: int | None = None,
        worker_timeout_s: float | None = None,
    ):
        self.memory_limit_mb = memory_limit_mb or settings.sandbox_memory_limit_mb
        self.fuel_limit = fuel_limit or settings.sandbox_fuel_limit
        self.max_output_bytes = max_output_bytes or settings.sandbox_max_output_bytes
        self.worker_timeout_s = worker_timeout_s or settings.sandbox_worker_timeout_s
        self.execution_mode = execution_mode or settings.sandbox_execution_mode
        self.allow_host_fallback = (
            settings.sandbox_allow_host_fallback if allow_host_fallback is None else allow_host_fallback
        )
        self.engine: Any | None = None

        if self.execution_mode not in {"isolated", "host"}:
            raise SandboxError("Execution mode must be 'isolated' or 'host'.")
        if settings.is_production and (self.execution_mode != "isolated" or self.allow_host_fallback):
            raise SandboxError("Production requires isolated execution with host fallback disabled.")
        if self.execution_mode == "host" and not self.allow_host_fallback:
            raise SandboxError("Host execution requires explicit allow_host_fallback=True.")
        if self.execution_mode == "isolated" and not self._worker_available():
            if not self.allow_host_fallback or settings.is_production:
                raise SandboxError("Isolated worker unavailable and host fallback is disabled.")
            logger.warning("Isolated worker unavailable; using explicit development host fallback.")
            self.execution_mode = "host"

        try:
            import wasmtime

            config = wasmtime.Config()
            config.consume_fuel = True
            self.engine = wasmtime.Engine(config)
        except ImportError:
            logger.info("Wasmtime not installed; WASI tools are unavailable.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Wasmtime initialisation failed: %s", exc)

    @staticmethod
    def _worker_available() -> bool:
        return bool(sys.executable and os.path.isfile(sys.executable) and os.access(sys.executable, os.X_OK))

    def execute(self, action: Any) -> SandboxResult:
        """Execute an action after validating its declared capabilities."""
        tool_def = self.validate(action)

        if tool_def.wasm_module_path:
            try:
                with open(tool_def.wasm_module_path, "rb") as module_file:
                    return self.run_wasm_module(module_file.read(), capabilities=tool_def.capabilities)
            except OSError as exc:
                raise SandboxError(f"Unable to load WASI module: {exc}") from exc

        if self.execution_mode == "isolated" and tool_def.isolated_builtin:
            return self._execute_worker(tool_def, action.arguments, compensation=False)

        if not self.allow_host_fallback or settings.is_production:
            raise SandboxError(
                f"Tool '{tool_def.name}' has no isolated backend; explicit development host fallback is disabled."
            )
        result = tool_def.handler(action.arguments)
        result.data.setdefault("isolation", {"backend": "host-development-fallback", "worker_pid": os.getpid()})
        return result

    def validate(self, action: Any) -> ToolDefinition:
        """Validate an action without crossing the external-effect boundary.

        Coordinators call this before writing an ``EXECUTING`` journal record,
        so deterministic registry, policy, and capability failures cannot be
        misclassified as ambiguous external effects.
        """
        tool_def = registry.get(action.tool_name)
        self._validate_policy(tool_def, action.arguments)
        self._validate_capabilities(tool_def, action.arguments)
        return tool_def

    def execute_compensation(self, compensation: Any) -> bool:
        """Run an allow-listed compensation using the same execution boundary."""
        tool_name = compensation.tool_name
        if not registry.is_compensation_allowed(tool_name):
            logger.error("Compensation tool '%s' is not in the allow-list.", tool_name)
            return False
        tool_def = registry.get(tool_name)
        if tool_def.compensation_handler is None:
            logger.error("Tool '%s' has no compensation handler registered.", tool_name)
            return False
        try:
            self._validate_policy(tool_def, compensation.arguments)
            self._validate_capabilities(tool_def, compensation.arguments)
            if self.execution_mode == "isolated" and tool_def.isolated_builtin:
                result = self._execute_worker(tool_def, compensation.arguments, compensation=True)
                return result.success
            if not self.allow_host_fallback or settings.is_production:
                logger.error("Compensation '%s' has no isolated backend.", tool_name)
                return False
            return tool_def.compensation_handler(compensation.arguments)
        except SandboxError as exc:
            logger.error("Compensation '%s' failed: %s", tool_name, exc)
            return False

    @staticmethod
    def _validate_policy(tool_def: ToolDefinition, arguments: Mapping[str, Any]) -> None:
        mutating = registry.is_mutating(tool_def)
        if not mutating:
            return
        if tool_def.policy is None:
            raise SandboxError(f"Mutating tool '{tool_def.name}' has no typed policy.")
        evaluator = ToolPolicyRegistry()
        evaluator.register(tool_def.policy)
        decision = evaluator.require(tool_def.name, dict(arguments), mutating=True)
        if not decision.allowed:
            detail = decision.violated_property or decision.status.value
            raise SandboxError(f"Typed policy rejected '{tool_def.name}' ({detail}): {decision.explanation}")

    def _validate_capabilities(self, tool_def: ToolDefinition, arguments: Mapping[str, Any]) -> None:
        manifest = tool_def.capabilities
        effective_memory = min(manifest.memory_mb, self.memory_limit_mb)
        if effective_memory < 64:
            raise SandboxError("Effective worker memory limit is too small to start safely.")

        for argument_name, access in tool_def.path_arguments.items():
            raw_path = arguments.get(argument_name)
            if raw_path is None:
                continue
            if not isinstance(raw_path, str):
                raise SandboxError(f"Path argument '{argument_name}' must be a string.")
            try:
                safe_path = contain_path(raw_path)
            except PathSecurityError as exc:
                raise SandboxError(str(exc)) from exc
            relative = os.path.relpath(safe_path, settings.allowed_workspace_root).replace(os.sep, "/")
            patterns = manifest.filesystem.read if access == "read" else manifest.filesystem.write
            if access not in {"read", "write"} or not _path_matches(relative, patterns):
                raise SandboxError(
                    f"Tool '{tool_def.name}' lacks {access!r} capability for workspace path '{relative}'."
                )

        # The Python worker protocol contains trusted built-ins only. Network
        # capabilities require a dedicated adapter/WASI implementation so they
        # cannot be claimed without enforcement.
        if tool_def.isolated_builtin and manifest.network:
            raise SandboxError("Python worker tools cannot request network capabilities.")

    def _execute_worker(
        self, tool_def: ToolDefinition, arguments: Mapping[str, Any], *, compensation: bool
    ) -> SandboxResult:
        manifest = tool_def.capabilities
        request = {
            "tool_name": tool_def.name,
            "arguments": dict(arguments),
            "compensation": compensation,
            "workspace_root": settings.allowed_workspace_root,
            "memory_mb": min(self.memory_limit_mb, manifest.memory_mb),
            "cpu_seconds": max(1, int(self.worker_timeout_s) + 1),
        }
        try:
            payload = json.dumps(request, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise SandboxError(f"Tool arguments are not JSON serializable: {exc}") from exc

        env = {"PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        for name in manifest.environment:
            if name in os.environ:
                env[name] = os.environ[name]

        try:
            completed = subprocess.run(  # noqa: S603 - current trusted interpreter
                [sys.executable, "-m", "src.orchestrator.sandbox_worker"],
                input=payload,
                capture_output=True,
                text=True,
                timeout=self.worker_timeout_s,
                check=False,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise SandboxError(f"Isolated worker exceeded {self.worker_timeout_s:g}s timeout.") from exc
        except OSError as exc:
            raise SandboxError(f"Unable to start isolated worker: {exc}") from exc

        encoded = completed.stdout.encode("utf-8", errors="replace")
        effective_output_limit = min(self.max_output_bytes, manifest.output_bytes)
        if len(encoded) > effective_output_limit:
            raise SandboxError(f"Isolated worker output exceeded {effective_output_limit} bytes.")
        if completed.returncode != 0:
            stderr = completed.stderr.strip()[:1_000]
            raise SandboxError(f"Isolated worker exited {completed.returncode}: {stderr or 'no diagnostic'}")
        try:
            response = json.loads(completed.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise SandboxError("Isolated worker returned malformed output.") from exc
        if not response.get("ok"):
            raise SandboxError(str(response.get("error", "isolated worker failed")))
        if compensation:
            return SandboxResult(success=bool(response.get("result")))

        result_data = response.get("result")
        if not isinstance(result_data, dict):
            raise SandboxError("Isolated worker returned an invalid result shape.")
        result = SandboxResult(**result_data)
        result.data.setdefault("isolation", {"backend": "isolated-worker", "worker_pid": response.get("worker_pid")})
        return result

    def run_wasm_module(
        self,
        wasm_bytes: bytes,
        entrypoint: str = "_start",
        preopen_root: str | None = None,
        capabilities: CapabilityManifest | None = None,
    ) -> SandboxResult:
        """Execute WASI bytecode with fuel, memory, and capability limits.

        WASI Preview 1 exposes no network sockets. Environment variables are
        copied only when named in the manifest. A workspace preopen is supplied
        only when filesystem access is declared.
        """
        if not self.engine:
            raise SandboxError("WASI runtime unavailable (install the 'wasm' extra: wasmtime).")
        import wasmtime

        manifest = capabilities or CapabilityManifest(
            fuel=self.fuel_limit,
            memory_mb=self.memory_limit_mb,
            output_bytes=self.max_output_bytes,
        )
        if manifest.network:
            raise SandboxError("WASI Preview 1 network capabilities are unsupported and denied.")
        root = preopen_root or settings.allowed_workspace_root
        store = wasmtime.Store(self.engine)
        store.set_fuel(min(self.fuel_limit, manifest.fuel))
        if hasattr(store, "set_limits"):
            store.set_limits(memory_size=min(self.memory_limit_mb, manifest.memory_mb) * 1024 * 1024)

        wasi = wasmtime.WasiConfig()
        env_pairs = [(name, os.environ[name]) for name in manifest.environment if name in os.environ]
        if env_pairs:
            wasi.env = env_pairs
        if manifest.filesystem.read or manifest.filesystem.write:
            if manifest.filesystem.read and not manifest.filesystem.write:
                try:
                    wasi.preopen_dir(
                        root,
                        "/",
                        dir_perms=wasmtime.DirPerms.READ,
                        file_perms=wasmtime.FilePerms.READ,
                    )
                except (AttributeError, TypeError) as exc:
                    raise SandboxError("Installed Wasmtime cannot enforce a read-only preopen.") from exc
            else:
                wasi.preopen_dir(root, "/")
        store.set_wasi(wasi)

        linker = wasmtime.Linker(self.engine)
        linker.define_wasi()
        try:
            module = wasmtime.Module(self.engine, wasm_bytes)
            instance = linker.instantiate(store, module)
            func = instance.exports(store).get(entrypoint)
            if func is None:
                raise SandboxError(f"WASI module has no export '{entrypoint}'.")
            func(store)
        except wasmtime.WasmtimeError as exc:
            raise SandboxError(f"WASI execution trapped: {exc}") from exc
        return SandboxResult(
            success=True,
            data={"entrypoint": entrypoint, "jail": root, "isolation": {"backend": "wasi"}},
        )


def worker_execute(request: Mapping[str, Any]) -> dict[str, Any]:
    """Execute one worker-protocol request. Called only by sandbox_worker."""
    tool_name = request.get("tool_name")
    if not isinstance(tool_name, str):
        raise SandboxError("Worker request has no valid tool_name.")
    tool_def = registry.get(tool_name)
    if not tool_def.isolated_builtin:
        raise SandboxError(f"Tool '{tool_name}' is not a bundled isolated worker tool.")
    arguments = request.get("arguments")
    if not isinstance(arguments, dict):
        raise SandboxError("Worker arguments must be an object.")
    if request.get("compensation"):
        if not registry.is_compensation_allowed(tool_name) or tool_def.compensation_handler is None:
            raise SandboxError(f"Tool '{tool_name}' is not allowed as compensation.")
        result: bool | dict[str, Any] = tool_def.compensation_handler(arguments)
    else:
        result = asdict(tool_def.handler(arguments))
    return {"ok": True, "result": result, "worker_pid": os.getpid()}
