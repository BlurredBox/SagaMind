"""Server-derived file rollback contracts preserve preimages and detect races."""

import pytest

from src.main import _derive_file_contract
from src.models import ActionPayload
from src.orchestrator.sandbox import SandboxError, WasmSandbox


def test_derived_contract_restores_overwrite_and_rejects_stale_forward(tmp_path, monkeypatch):
    from src import config, security

    monkeypatch.setattr(config.settings, "allowed_workspace_root", str(tmp_path))
    monkeypatch.setattr(security.settings, "allowed_workspace_root", str(tmp_path))
    target = tmp_path / "document.txt"
    target.write_text("original")

    guarded, compensation = _derive_file_contract(
        "WRITE_FILE",
        {"path": str(target), "content": "replacement"},
    )
    assert compensation.tool_name == "RESTORE_FILE"
    assert compensation.arguments["previous"] == "original"

    target.write_text("concurrent-change")
    with pytest.raises(SandboxError, match="precondition failed"):
        WasmSandbox().execute(ActionPayload("WRITE_FILE", guarded))
    assert target.read_text() == "concurrent-change"
