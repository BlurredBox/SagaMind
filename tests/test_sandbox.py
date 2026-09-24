"""
SagaMind — Sandbox Tests
========================

Validates the filesystem jail, tool allow-list, typed results, and compensation logic.
"""

import importlib.util
import os

import pytest

from src.models import ActionPayload, SandboxResult
from src.orchestrator.sandbox import (
    CapabilityManifest,
    FilesystemCapabilities,
    SandboxError,
    ToolDefinition,
    WasmSandbox,
    registry,
)
from src.policy import ArgumentRule, ToolPolicy, ValueKind


@pytest.fixture
def jailed_sandbox(tmp_path, monkeypatch):
    """A sandbox whose workspace jail is an isolated temp directory."""
    from src import config, security

    monkeypatch.setattr(config.settings, "allowed_workspace_root", str(tmp_path))
    monkeypatch.setattr(security.settings, "allowed_workspace_root", str(tmp_path))
    return WasmSandbox(), tmp_path


class TestWriteFile:
    def test_preflight_has_no_external_effect(self, jailed_sandbox):
        sandbox, root = jailed_sandbox
        target = root / "preflight.txt"

        definition = sandbox.validate(ActionPayload("WRITE_FILE", {"path": str(target), "content": "not-yet"}))

        assert definition.name == "WRITE_FILE"
        assert not target.exists()

    def test_write_inside_jail_succeeds(self, jailed_sandbox):
        sandbox, root = jailed_sandbox
        target = root / "sub" / "out.txt"
        result = sandbox.execute(ActionPayload("WRITE_FILE", {"path": str(target), "content": "hello"}))
        assert isinstance(result, SandboxResult)
        assert result.success is True
        assert target.read_text() == "hello"

    def test_write_outside_jail_rejected(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        with pytest.raises(SandboxError):
            sandbox.execute(ActionPayload("WRITE_FILE", {"path": "/etc/passwd", "content": "x"}))

    def test_traversal_rejected(self, jailed_sandbox):
        sandbox, root = jailed_sandbox
        with pytest.raises(SandboxError):
            sandbox.execute(ActionPayload("WRITE_FILE", {"path": str(root / ".." / "x.txt"), "content": "x"}))

    def test_missing_path_rejected(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        with pytest.raises(SandboxError):
            sandbox.execute(ActionPayload("WRITE_FILE", {"content": "x"}))


class TestToolAllowList:
    def test_unknown_tool_rejected(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        with pytest.raises(SandboxError):
            sandbox.execute(ActionPayload("RM_RF", {"path": "/"}))

    def test_unimplemented_database_query_is_rejected(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        with pytest.raises(SandboxError, match="not registered"):
            sandbox.execute(ActionPayload("DATABASE_QUERY", {"query": "SELECT 1"}))

    def test_stale_write_precondition_is_rejected(self, jailed_sandbox):
        sandbox, root = jailed_sandbox
        target = root / "stale.txt"
        target.write_text("changed")
        with pytest.raises(SandboxError, match="precondition failed"):
            sandbox.execute(
                ActionPayload(
                    "WRITE_FILE",
                    {"path": str(target), "content": "new", "expected_sha256": "0" * 64},
                )
            )
        assert target.read_text() == "changed"

    def test_builtin_runs_in_separate_worker_by_default(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        result = sandbox.execute(ActionPayload("NOOP", {}))
        isolation = result.data["isolation"]
        assert isolation["backend"] == "isolated-worker"
        assert isolation["worker_pid"] != os.getpid()

    def test_custom_python_tool_is_not_mislabeled_as_isolated(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        tool = ToolDefinition("CUSTOM_HOST_ONLY", lambda _: SandboxResult(success=True), mutating=False)
        registry.register(tool)
        try:
            with pytest.raises(SandboxError, match="no isolated backend"):
                sandbox.execute(ActionPayload(tool.name, {}))
        finally:
            registry.unregister(tool.name)

    def test_explicit_development_host_fallback(self, jailed_sandbox):
        _, _root = jailed_sandbox
        sandbox = WasmSandbox(execution_mode="host", allow_host_fallback=True)
        result = sandbox.execute(ActionPayload("NOOP", {}))
        assert result.data["isolation"] == {
            "backend": "host-development-fallback",
            "worker_pid": os.getpid(),
        }


class TestCapabilityManifest:
    def test_manifest_exposes_all_resource_classes(self):
        manifest = CapabilityManifest(
            filesystem=FilesystemCapabilities(read=("input/**",), write=("output/**",)),
            network=("api.example.test:443",),
            environment=("SAFE_TOKEN",),
            fuel=123,
            memory_mb=128,
            output_bytes=4096,
        )
        assert manifest.filesystem.read == ("input/**",)
        assert manifest.network == ("api.example.test:443",)
        assert manifest.environment == ("SAFE_TOKEN",)
        assert (manifest.fuel, manifest.memory_mb, manifest.output_bytes) == (123, 128, 4096)

    def test_path_outside_manifest_glob_is_denied_before_execution(self, jailed_sandbox):
        sandbox, root = jailed_sandbox
        tool = ToolDefinition(
            "NARROW_WRITE",
            lambda _: SandboxResult(success=True),
            capabilities=CapabilityManifest(filesystem=FilesystemCapabilities(write=("generated/**",))),
            path_arguments={"path": "write"},
            mutating=True,
            policy=ToolPolicy("NARROW_WRITE", True, {"path": ArgumentRule(ValueKind.PATH)}),
        )
        registry.register(tool)
        try:
            with pytest.raises(SandboxError, match="lacks 'write' capability"):
                sandbox.execute(ActionPayload(tool.name, {"path": str(root / "private" / "x")}))
        finally:
            registry.unregister(tool.name)

    def test_mutating_tool_without_policy_cannot_be_registered(self):
        tool = ToolDefinition(
            "UNPOLICIED_WRITE",
            lambda _: SandboxResult(success=True),
            capabilities=CapabilityManifest(filesystem=FilesystemCapabilities(write=("**",))),
            mutating=True,
        )

        with pytest.raises(SandboxError, match="requires a typed policy"):
            registry.register(tool)

    def test_policy_rejects_nested_argument_before_execution(self, jailed_sandbox):
        sandbox, root = jailed_sandbox

        with pytest.raises(SandboxError, match="argument.content.type"):
            sandbox.execute(ActionPayload("WRITE_FILE", {"path": str(root / "x"), "content": {"nested": True}}))

    def test_network_capability_is_denied_for_python_worker(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        tool = ToolDefinition(
            "BAD_NETWORK_BUILTIN",
            lambda _: SandboxResult(success=True),
            capabilities=CapabilityManifest(network=("example.test:443",)),
            isolated_builtin=True,
            mutating=False,
        )
        registry.register(tool)
        try:
            with pytest.raises(SandboxError, match="cannot request network"):
                sandbox.execute(ActionPayload(tool.name, {}))
        finally:
            registry.unregister(tool.name)


class TestCompensation:
    def test_delete_file_inside_jail(self, jailed_sandbox):
        sandbox, root = jailed_sandbox
        target = root / "f.txt"
        target.write_text("data")
        ok = sandbox.execute_compensation(ActionPayload("DELETE_FILE", {"path": str(target)}))
        assert ok is True
        assert not target.exists()

    def test_delete_missing_file_is_noop_success(self, jailed_sandbox):
        sandbox, root = jailed_sandbox
        ok = sandbox.execute_compensation(ActionPayload("DELETE_FILE", {"path": str(root / "nope.txt")}))
        assert ok is True

    def test_delete_outside_jail_rejected(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        ok = sandbox.execute_compensation(ActionPayload("DELETE_FILE", {"path": "/etc/hosts"}))
        assert ok is False

    def test_unknown_compensation_rejected(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        assert sandbox.execute_compensation(ActionPayload("WIPE_DISK", {})) is False

    def test_restore_file_reinstates_overwritten_preimage(self, jailed_sandbox):
        sandbox, root = jailed_sandbox
        target = root / "existing.txt"
        target.write_text("mutated")

        ok = sandbox.execute_compensation(
            ActionPayload(
                "RESTORE_FILE",
                {"path": str(target), "existed": True, "previous": "original"},
            )
        )

        assert ok is True
        assert target.read_text() == "original"

    def test_restore_file_removes_new_file(self, jailed_sandbox):
        sandbox, root = jailed_sandbox
        target = root / "new.txt"
        target.write_text("created")

        ok = sandbox.execute_compensation(
            ActionPayload(
                "RESTORE_FILE",
                {"path": str(target), "existed": False, "previous": ""},
            )
        )

        assert ok is True
        assert not target.exists()

    def test_restore_file_outside_jail_fails_closed(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        assert (
            sandbox.execute_compensation(
                ActionPayload(
                    "RESTORE_FILE",
                    {"path": "/etc/hosts", "existed": True, "previous": "forbidden"},
                )
            )
            is False
        )


class TestWasmIsolation:
    def test_run_wasm_requires_runtime_when_absent(self, jailed_sandbox):
        sandbox, _ = jailed_sandbox
        if importlib.util.find_spec("wasmtime") is not None:
            pytest.skip("wasmtime present; runtime-absent path not exercised")
        with pytest.raises(SandboxError):
            sandbox.run_wasm_module(b"\x00asm\x01\x00\x00\x00")
