"""Fail when any safety-critical source file is below the coverage threshold."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path

DEFAULT_FILES = (
    "src/orchestrator/coordinator.py",
    "src/orchestrator/sandbox.py",
    "src/policy.py",
    "src/security.py",
    "src/verifier/z3_prover.py",
)

_MEMORY_STORE_METHODS = {
    "write_transaction_state",
    "append_compensation",
    "prepare_effect",
    "transition_effect",
    "commit_effect",
    "get_effect",
    "get_effects",
    "_now",
    "_assert_same_effect",
    "_memory_effect",
    "step_already_committed",
    "mark_step_committed",
    "push_dead_letter",
    "list_dead_letters",
    "record_step",
    "get_history",
    "list_incomplete",
    "close",
}


def _critical_files(root: Path) -> list[str]:
    files = list(DEFAULT_FILES)
    files.extend(str(path.relative_to(root)) for path in sorted((root / "src/contracts").glob("*.py")))
    return files


def _memory_path_statement_lines(path: Path) -> set[int]:
    """Return statements executed by the in-memory state-store implementation.

    The production adapters share one facade with the memory implementation. We
    count the common dispatch statements and memory fall-through, while excluding
    bodies guarded by ``self.backend == postgres/redis``. This makes the scoped
    threshold inspectable instead of hiding optional adapters with coverage omits.
    """

    module = ast.parse(path.read_text(encoding="utf-8"))
    store_class = next(node for node in module.body if isinstance(node, ast.ClassDef) and node.name == "SagaStateStore")
    lines: set[int] = set()

    def collect(statement: ast.stmt) -> None:
        lines.add(statement.lineno)
        if isinstance(statement, ast.If) and "self.backend" in ast.unparse(statement.test):
            for item in statement.orelse:
                backend_branch = isinstance(item, ast.If) and "self.backend" in ast.unparse(item.test)
                if backend_branch or not isinstance(item, ast.If):
                    collect(item)
            return
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt):
                collect(child)

    for node in store_class.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in _MEMORY_STORE_METHODS:
            for statement in node.body:
                collect(statement)
    return lines


def _coverage_entry(measured: dict[str, object], relative: str) -> dict[str, object] | None:
    entry = measured.get(relative)
    return entry if isinstance(entry, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, nargs="?", default=Path("coverage.json"))
    parser.add_argument("--minimum", type=float, default=85.0)
    args = parser.parse_args()

    root = Path.cwd()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    measured: dict[str, object] = report["files"]
    failures: list[str] = []
    for relative in _critical_files(root):
        entry = _coverage_entry(measured, relative)
        if entry is None:
            failures.append(f"{relative}: absent from coverage report")
            continue
        summary = entry.get("summary")
        if not isinstance(summary, dict) or "percent_covered" not in summary:
            failures.append(f"{relative}: malformed coverage entry")
            continue
        percent = float(summary["percent_covered"])
        print(f"{relative}: {percent:.2f}%")
        if percent + 1e-9 < args.minimum:
            failures.append(f"{relative}: {percent:.2f}% < {args.minimum:.2f}%")

    state_store = "src/orchestrator/state_store.py"
    entry = _coverage_entry(measured, state_store)
    if entry is None:
        failures.append(f"{state_store} memory path: absent from coverage report")
    else:
        executed = set(entry.get("executed_lines", []))
        missing = set(entry.get("missing_lines", []))
        scoped = _memory_path_statement_lines(root / state_store) & (executed | missing)
        percent = 100.0 * len(scoped & executed) / len(scoped) if scoped else 0.0
        print(f"{state_store} memory path: {percent:.2f}%")
        if percent + 1e-9 < args.minimum:
            failures.append(f"{state_store} memory path: {percent:.2f}% < {args.minimum:.2f}%")

    if failures:
        print("Critical coverage gate failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print(f"Every critical file meets the {args.minimum:.2f}% coverage floor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
