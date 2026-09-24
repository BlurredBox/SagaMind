"""Private JSON worker protocol for bundled SagaMind tool handlers."""

from __future__ import annotations

import json
import os
import sys
from typing import Any


def _apply_resource_limits(request: dict[str, Any]) -> None:
    """Apply OS limits; unsupported platforms retain their parent limits."""
    try:
        import resource
    except ImportError:
        return

    cpu_seconds = max(1, int(request.get("cpu_seconds", 1)))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    if hasattr(resource, "RLIMIT_NOFILE"):
        _soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        target = min(32, hard if hard != resource.RLIM_INFINITY else 32)
        resource.setrlimit(resource.RLIMIT_NOFILE, (target, target))

    # RLIMIT_AS is reliable on Linux. macOS counts mapped shared libraries in
    # ways that can kill a healthy interpreter at modest limits, so memory is
    # enforceable there only for WASI modules.
    if sys.platform.startswith("linux") and hasattr(resource, "RLIMIT_AS"):
        memory_bytes = int(request.get("memory_mb", 256)) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        if not isinstance(request, dict):
            raise ValueError("request must be a JSON object")
        _apply_resource_limits(request)

        from src import config
        from src.orchestrator.sandbox import worker_execute

        workspace_root = request.get("workspace_root")
        if not isinstance(workspace_root, str) or not os.path.isabs(workspace_root):
            raise ValueError("workspace_root must be an absolute path")
        config.settings.allowed_workspace_root = workspace_root
        response = worker_execute(request)
    except BaseException as exc:  # noqa: BLE001 - serialize all worker failures
        response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    sys.stdout.write(json.dumps(response, separators=(",", ":"), allow_nan=False))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
