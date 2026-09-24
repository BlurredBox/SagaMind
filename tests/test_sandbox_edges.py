"""Adversarial tests for the isolation protocol and capability failure paths."""

import json
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.contracts import implementation_hash
from src.models import ActionPayload, SandboxResult
from src.orchestrator.sandbox import (
    CapabilityManifest,
    FilesystemCapabilities,
    SandboxError,
    ToolDefinition,
    ToolRegistry,
    WasmSandbox,
    _check_file_precondition,
    _comp_delete_file,
    _comp_noop,
    _comp_restore_file,
    _file_sha256,
    _handle_delete_file,
    _handle_restore_file,
    _handle_write_file,
    registry,
    worker_execute,
)
from src.policy import ArgumentRule, ToolPolicy, ValueKind


@pytest.fixture
def jail(tmp_path, monkeypatch):
    from src import config, policy, security

    monkeypatch.setattr(config.settings, "allowed_workspace_root", str(tmp_path))
    monkeypatch.setattr(security.settings, "allowed_workspace_root", str(tmp_path))
    monkeypatch.setattr(policy, "contain_path", security.contain_path)
    return tmp_path


def test_manifest_and_registry_reject_invalid_contracts():
    with pytest.raises(ValueError, match="positive"):
        CapabilityManifest(fuel=0)
    with pytest.raises(ValueError, match="variable names"):
        CapabilityManifest(environment=("BAD=value",))

    local = ToolRegistry()
    with pytest.raises(SandboxError, match="explicitly declare"):
        local.register(ToolDefinition("UNCLASSIFIED", lambda _: SandboxResult(True)))
    wrong = ToolPolicy("OTHER", True, {"path": ArgumentRule(ValueKind.PATH)})
    with pytest.raises(SandboxError, match="cannot be attached"):
        local.register(
            ToolDefinition(
                "WRITE",
                lambda _: SandboxResult(True),
                mutating=True,
                policy=wrong,
            )
        )
    local.register(ToolDefinition("READ", lambda _: SandboxResult(True), mutating=False), allow_as_compensation=True)
    assert local.allowed_actions == frozenset({"READ"})
    assert local.allowed_compensations == frozenset({"READ"})


def test_verified_compensation_registration_is_implementation_bound():
    policy = ToolPolicy("VERIFIED_WRITE", True, {"path": ArgumentRule(ValueKind.PATH)})
    valid_certificate = SimpleNamespace(proved=True, implementation_hash=implementation_hash("handler:v1"))
    local = ToolRegistry()
    local.register(
        ToolDefinition(
            "VERIFIED_WRITE",
            lambda _: SandboxResult(True),
            mutating=True,
            policy=policy,
            requires_verified_compensation=True,
            compensation_certificate=valid_certificate,
            implementation_identity="handler:v1",
        )
    )
    assert "VERIFIED_WRITE" in local.allowed_actions

    for certificate, identity, message in (
        (None, "handler:v1", "proved compensation"),
        (SimpleNamespace(proved=False), "handler:v1", "proved compensation"),
        (valid_certificate, None, "implementation identity"),
        (valid_certificate, "handler:v2", "another implementation"),
    ):
        with pytest.raises(SandboxError, match=message):
            ToolRegistry().register(
                ToolDefinition(
                    "VERIFIED_WRITE",
                    lambda _: SandboxResult(True),
                    mutating=True,
                    policy=policy,
                    requires_verified_compensation=True,
                    compensation_certificate=certificate,
                    implementation_identity=identity,
                )
            )


def test_host_handlers_write_and_delete(jail):
    sandbox = WasmSandbox(execution_mode="host", allow_host_fallback=True)
    target = jail / "direct.txt"

    written = sandbox.execute(ActionPayload("WRITE_FILE", {"path": str(target), "content": "hello"}))
    deleted = sandbox.execute_compensation(ActionPayload("DELETE_FILE", {"path": str(target)}))

    assert written.data["bytes"] == 5
    assert deleted is True
    assert not target.exists()


def test_file_handlers_enforce_preimages_and_restore_both_states(jail):
    target = jail / "preimage.txt"
    target.write_text("before", encoding="utf-8")
    before_hash = _file_sha256(str(target))

    with pytest.raises(SandboxError, match="Only one file precondition"):
        _check_file_precondition(
            str(target),
            {"expected_sha256": before_hash, "expected_absent": True},
        )
    with pytest.raises(SandboxError, match="expected path to be absent"):
        _check_file_precondition(str(target), {"expected_absent": True})
    with pytest.raises(SandboxError, match="content hash changed"):
        _check_file_precondition(str(target), {"expected_sha256": "0" * 64})

    _handle_write_file({"path": str(target), "content": "after", "expected_sha256": before_hash})
    after_hash = _file_sha256(str(target))
    restored = _handle_restore_file(
        {
            "path": str(target),
            "existed": True,
            "previous": "before",
            "expected_sha256": after_hash,
        }
    )
    assert restored.data["restored"] is True
    assert target.read_text(encoding="utf-8") == "before"

    created = jail / "created.txt"
    created.write_text("temporary", encoding="utf-8")
    removed = _handle_restore_file(
        {
            "path": str(created),
            "existed": False,
            "expected_sha256": _file_sha256(str(created)),
        }
    )
    assert removed.data["restored"] is True
    assert not created.exists()


def test_file_handler_failure_paths_are_closed_and_cleanup_temp_files(jail, monkeypatch):
    with pytest.raises(SandboxError, match="requires a 'path'"):
        _handle_write_file({"content": "x"})
    with pytest.raises(SandboxError, match="must be a string"):
        _handle_write_file({"path": str(jail / "x"), "content": 1})
    with pytest.raises(SandboxError):
        _handle_write_file({"path": str(jail.parent / "escape"), "content": "x"})

    target = jail / "atomic.txt"
    monkeypatch.setattr("src.orchestrator.sandbox.os.replace", lambda *_: (_ for _ in ()).throw(OSError("replace")))
    with pytest.raises(SandboxError, match="Failed to write file"):
        _handle_write_file({"path": str(target), "content": "x"})
    assert list(jail.glob(".sagamind-*")) == []

    assert _handle_delete_file({}).success is True
    assert _handle_delete_file({"path": str(jail / "missing")}).success is True
    with pytest.raises(SandboxError):
        _handle_delete_file({"path": str(jail.parent / "escape")})


def test_compensation_handler_failure_paths(jail, monkeypatch):
    target = jail / "delete.txt"
    target.write_text("x", encoding="utf-8")
    assert _comp_delete_file({}) is True
    assert _comp_delete_file({"path": str(jail / "missing")}) is True
    assert _comp_delete_file({"path": str(jail.parent / "escape")}) is False

    monkeypatch.setattr("src.orchestrator.sandbox.os.remove", lambda *_: (_ for _ in ()).throw(OSError("denied")))
    assert _comp_delete_file({"path": str(target)}) is False
    assert _comp_restore_file({"path": str(jail.parent / "escape"), "existed": False}) is False
    assert _comp_noop({}) is True


def test_direct_worker_protocol_validates_shape_and_compensation(jail):
    target = jail / "worker.txt"
    response = worker_execute({"tool_name": "WRITE_FILE", "arguments": {"path": str(target), "content": "x"}})
    assert response["ok"] is True
    assert target.read_text(encoding="utf-8") == "x"
    assert (
        worker_execute({"tool_name": "DELETE_FILE", "arguments": {"path": str(target)}, "compensation": True})["result"]
        is True
    )

    with pytest.raises(SandboxError, match="valid tool_name"):
        worker_execute({"arguments": {}})
    with pytest.raises(SandboxError, match="must be an object"):
        worker_execute({"tool_name": "NOOP", "arguments": []})
    with pytest.raises(SandboxError, match="not allowed as compensation"):
        worker_execute({"tool_name": "WRITE_FILE", "arguments": {}, "compensation": True})


def _completed(stdout: str, *, returncode: int = 0, stderr: str = ""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


@pytest.mark.parametrize(
    ("side_effect", "response", "message"),
    [
        (subprocess.TimeoutExpired("worker", 1), None, "exceeded"),
        (OSError("spawn failed"), None, "Unable to start"),
        (None, _completed("x" * 2000), "output exceeded"),
        (None, _completed("", returncode=7, stderr="boom"), "exited 7"),
        (None, _completed("not json"), "malformed"),
        (None, _completed(json.dumps({"ok": False, "error": "denied"})), "denied"),
        (None, _completed(json.dumps({"ok": True, "result": []})), "invalid result shape"),
    ],
)
def test_worker_failures_are_fail_closed(side_effect, response, message, jail):
    sandbox = WasmSandbox(max_output_bytes=1024)
    kwargs = {"side_effect": side_effect} if side_effect is not None else {"return_value": response}
    with patch("src.orchestrator.sandbox.subprocess.run", **kwargs), pytest.raises(SandboxError, match=message):
        sandbox._execute_worker(registry.get("NOOP"), {}, compensation=False)


def test_worker_rejects_nonserializable_arguments_and_handles_compensation(jail):
    sandbox = WasmSandbox()
    with pytest.raises(SandboxError, match="not JSON serializable"):
        sandbox._execute_worker(registry.get("NOOP"), {"bad": object()}, compensation=False)

    response = _completed(json.dumps({"ok": True, "result": True, "worker_pid": 1}))
    with patch("src.orchestrator.sandbox.subprocess.run", return_value=response):
        assert sandbox._execute_worker(registry.get("NOOP"), {}, compensation=True).success is True


def test_initialization_and_capability_errors_fail_closed(jail, monkeypatch):
    with pytest.raises(SandboxError, match="must be 'isolated' or 'host'"):
        WasmSandbox(execution_mode="invalid")
    with pytest.raises(SandboxError, match="explicit allow_host_fallback"):
        WasmSandbox(execution_mode="host", allow_host_fallback=False)

    monkeypatch.setattr(WasmSandbox, "_worker_available", staticmethod(lambda: False))
    with pytest.raises(SandboxError, match="worker unavailable"):
        WasmSandbox(execution_mode="isolated", allow_host_fallback=False)
    assert WasmSandbox(execution_mode="isolated", allow_host_fallback=True).execution_mode == "host"

    sandbox = WasmSandbox(execution_mode="host", allow_host_fallback=True)
    low_memory = ToolDefinition(
        "LOW_MEMORY",
        lambda _: SandboxResult(True),
        capabilities=CapabilityManifest(memory_mb=32),
        mutating=False,
    )
    with pytest.raises(SandboxError, match="memory limit"):
        sandbox._validate_capabilities(low_memory, {})
    bad_access = ToolDefinition(
        "BAD_ACCESS",
        lambda _: SandboxResult(True),
        path_arguments={"path": "execute"},
        mutating=False,
    )
    with pytest.raises(SandboxError, match="lacks 'execute'"):
        sandbox._validate_capabilities(bad_access, {"path": str(jail / "x")})


def test_missing_wasm_file_and_untrusted_worker_tool_fail_closed(jail):
    tool = ToolDefinition(
        "MISSING_WASM",
        lambda _: SandboxResult(True),
        wasm_module_path=str(jail / "none.wasm"),
        mutating=False,
    )
    registry.register(tool)
    try:
        with pytest.raises(SandboxError, match="Unable to load"):
            WasmSandbox().execute(ActionPayload(tool.name, {}))
        with pytest.raises(SandboxError, match="not a bundled"):
            worker_execute({"tool_name": tool.name, "arguments": {}})
    finally:
        registry.unregister(tool.name)


def _fake_wasmtime(*, export=True, trap=False, readonly_supported=True):
    class FakeError(Exception):
        pass

    class Store:
        def __init__(self, _engine):
            self.fuel = None
            self.memory = None

        def set_fuel(self, fuel):
            self.fuel = fuel

        def set_limits(self, *, memory_size):
            self.memory = memory_size

        def set_wasi(self, _wasi):
            return None

    class WasiConfig:
        def preopen_dir(self, *_args, **kwargs):
            if kwargs and not readonly_supported:
                raise TypeError("unsupported")

    class Exports:
        def get(self, _entrypoint):
            if not export:
                return None

            def run(_store):
                if trap:
                    raise FakeError("trap")

            return run

    class Instance:
        def exports(self, _store):
            return Exports()

    class Linker:
        def __init__(self, _engine):
            pass

        def define_wasi(self):
            return None

        def instantiate(self, _store, _module):
            return Instance()

    return SimpleNamespace(
        Store=Store,
        WasiConfig=WasiConfig,
        Linker=Linker,
        Module=lambda _engine, _bytes: object(),
        WasmtimeError=FakeError,
        DirPerms=SimpleNamespace(READ="read"),
        FilePerms=SimpleNamespace(READ="read"),
    )


def test_wasi_capabilities_success_and_network_denial(jail, monkeypatch):
    fake = _fake_wasmtime()
    monkeypatch.setitem(sys.modules, "wasmtime", fake)
    sandbox = WasmSandbox()
    sandbox.engine = object()
    manifest = CapabilityManifest(
        filesystem=FilesystemCapabilities(read=("**",)),
        environment=("VISIBLE_TO_WASI",),
        fuel=10,
        memory_mb=64,
    )
    monkeypatch.setenv("VISIBLE_TO_WASI", "yes")

    result = sandbox.run_wasm_module(b"wasm", capabilities=manifest)

    assert result.success is True
    assert result.data["isolation"]["backend"] == "wasi"
    with pytest.raises(SandboxError, match="network capabilities"):
        sandbox.run_wasm_module(b"wasm", capabilities=CapabilityManifest(network=("example:443",)))


def test_wasi_readonly_preopen_missing_export_and_trap_fail_closed(jail, monkeypatch):
    sandbox = WasmSandbox()
    sandbox.engine = object()
    readonly = CapabilityManifest(filesystem=FilesystemCapabilities(read=("**",)))

    monkeypatch.setitem(sys.modules, "wasmtime", _fake_wasmtime(readonly_supported=False))
    with pytest.raises(SandboxError, match="read-only preopen"):
        sandbox.run_wasm_module(b"wasm", capabilities=readonly)

    monkeypatch.setitem(sys.modules, "wasmtime", _fake_wasmtime(export=False))
    with pytest.raises(SandboxError, match="no export"):
        sandbox.run_wasm_module(b"wasm")

    monkeypatch.setitem(sys.modules, "wasmtime", _fake_wasmtime(trap=True))
    with pytest.raises(SandboxError, match="execution trapped"):
        sandbox.run_wasm_module(b"wasm")
