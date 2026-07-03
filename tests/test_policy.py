"""Policy config parsing and CLI tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docpull.cli import main
from docpull.policy import PolicyConfig, PolicyError


def test_policy_config_parses_valid_yaml_and_explains(tmp_path: Path) -> None:
    policy_path = tmp_path / "docpull.policy.yml"
    policy_path.write_text(
        """
schema_version: 1
allowed_domains:
  - docs.example.com
denied_paths:
  - /admin/*
max_pages: 20
providers:
  allowed:
    - local
  max_estimated_cost_usd: 0.1
budget:
  maximum_paid_cost_usd: 0
auth:
  allow_authenticated_sources: false
redaction:
  enabled: true
  backend: hybrid
  language: en
  entities:
    - email_address
  score_threshold: 0.7
  patterns:
    - name: email
      regex: "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+"
""",
        encoding="utf-8",
    )

    policy = PolicyConfig.from_file(policy_path)

    assert policy.allowed_domains == ["docs.example.com"]
    assert policy.allows_url("https://docs.example.com/api")[0] is True
    assert policy.allows_url("https://docs.example.com/admin/users") == (False, "path_denied")
    assert policy.budget.maximum_paid_cost_usd == 0
    assert policy.redaction.backend == "hybrid"
    assert policy.redaction.entities == ["EMAIL_ADDRESS"]
    assert policy.redaction.score_threshold == 0.7
    assert any("allowed domains: docs.example.com" in line for line in policy.explain())
    assert any("paid budget: $0.0000" in line for line in policy.explain())


def test_policy_rejects_negative_budget(tmp_path: Path) -> None:
    policy_path = tmp_path / "bad-budget.yml"
    policy_path.write_text(
        """
schema_version: 1
budget:
  maximum_paid_cost_usd: -1
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError) as exc_info:
        PolicyConfig.from_file(policy_path)

    assert "maximum_paid_cost_usd" in str(exc_info.value)


def test_policy_rejects_url_shaped_domains_and_secret_provider_options(tmp_path: Path) -> None:
    policy_path = tmp_path / "bad.policy.yml"
    policy_path.write_text(
        """
schema_version: 1
allowed_domains:
  - https://docs.example.com/path
providers:
  provider_options:
    api_key: test-secret
""",
        encoding="utf-8",
    )

    with pytest.raises(PolicyError) as exc_info:
        PolicyConfig.from_file(policy_path)

    message = str(exc_info.value)
    assert "domain entries must be hostnames" in message
    assert "api_key" in message
    assert "test-secret" not in message


def test_policy_cli_validate_and_explain_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    policy_path = tmp_path / "policy.yml"
    policy_path.write_text(
        """
schema_version: 1
allowed_domains:
  - docs.example.com
""",
        encoding="utf-8",
    )

    assert main(["policy", "validate", str(policy_path), "--json"]) == 0
    validate_payload = json.loads(capsys.readouterr().out)
    assert validate_payload["valid"] is True
    assert validate_payload["source_policy"]["constraints"]["allowed_domains"] == ["docs.example.com"]

    assert main(["policy", "explain", str(policy_path), "--json"]) == 0
    explain_payload = json.loads(capsys.readouterr().out)
    assert any("allowed domains: docs.example.com" in line for line in explain_payload["explain"])
