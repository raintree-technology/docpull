from __future__ import annotations

import sys
from pathlib import Path

import pytest

from docpull_bench.adapters import AdapterError, CommandAdapter
from docpull_bench.models import ExtractInput


def test_command_adapter_requires_explicit_environment_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNDECLARED_SECRET", "must-not-leak")
    adapter = CommandAdapter(
        system="vendor",
        version="1",
        command=f"{sys.executable} helper.py --input {{input}} --output {{output}}",
    )
    assert "UNDECLARED_SECRET" not in adapter.public_config()["allowed_env_names"]
    assert "must-not-leak" not in str(adapter.public_config())


def test_command_adapter_missing_allowlisted_value_fails_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DECLARED_TOKEN", raising=False)
    adapter = CommandAdapter(
        system="vendor",
        version="1",
        command="vendor --input {input} --output {output}",
        allowed_env=["DECLARED_TOKEN"],
    )
    inputs = ExtractInput(case_id="extract.example", lane="extract", url="https://example.com")
    with pytest.raises(AdapterError, match="DECLARED_TOKEN"):
        adapter.preflight([inputs], repeat=1)


def test_command_requires_both_contract_placeholders() -> None:
    with pytest.raises(AdapterError):
        CommandAdapter(system="vendor", version="1", command="vendor --input {input}")


def test_command_cannot_override_identity_and_receives_no_undeclared_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    helper = tmp_path / "helper.py"
    helper.write_text(
        """
import json, os, sys
output = sys.argv[2]
payload = {
    "schema_version": 2,
    "case_id": "forged",
    "system": "forged",
    "status": "completed",
    "payload": {"kind": "checks", "details": {"secret_present": "UNDECLARED_SECRET" in os.environ}},
    "elapsed_seconds": 999,
    "adapter_version": "forged",
}
open(output, "w", encoding="utf-8").write(json.dumps(payload))
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("UNDECLARED_SECRET", "must-not-leak")
    adapter = CommandAdapter(
        system="vendor",
        version="1",
        command=f"{sys.executable} {helper} {{input}} {{output}}",
    )
    inputs = ExtractInput(case_id="extract.example", lane="extract", url="https://example.com")
    observation = adapter.run(inputs, tmp_path / "artifacts")
    assert observation.case_id == inputs.case_id
    assert observation.system == "vendor"
    assert observation.adapter_version == "1"
    assert observation.elapsed_seconds < 999
    assert observation.payload and observation.payload.details["secret_present"] is False
