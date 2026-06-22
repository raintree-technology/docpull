"""Tests for policy file validation."""

from __future__ import annotations

import json
from pathlib import Path

from docpull.cli import main
from docpull.policy import PolicyConfig


def test_policy_validate_json_outputs_non_secret_source_policy(tmp_path: Path, capsys) -> None:
    policy_path = tmp_path / "docpull.policy.yml"
    policy_path.write_text(
        """
schema_version: 1
allowed_domains:
  - docs.example.com
denied_paths:
  - /admin/*
providers:
  allowed:
    - local
  max_estimated_cost_usd: 0
auth:
  allow_authenticated_sources: false
""",
        encoding="utf-8",
    )

    assert main(["policy", "validate", str(policy_path), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["source_policy"]["constraints"]["allowed_domains"] == ["docs.example.com"]
    assert "secret" in payload["source_policy"]["secret_handling"].lower()


def test_policy_rejects_secret_like_provider_options(tmp_path: Path) -> None:
    policy_path = tmp_path / "bad.yml"
    policy_path.write_text(
        """
schema_version: 1
providers:
  provider_options:
    api_key: should-not-persist
""",
        encoding="utf-8",
    )

    assert main(["policy", "validate", str(policy_path)]) == 1


def test_policy_allows_and_blocks_urls() -> None:
    policy = PolicyConfig(allowed_domains=["docs.example.com"], denied_paths=["/admin/*"])

    assert policy.allows_url("https://docs.example.com/api")[0] is True
    assert policy.allows_url("https://docs.example.com/admin/users") == (False, "path_denied")
    assert policy.allows_url("https://example.com/api") == (False, "domain_not_allowed")
