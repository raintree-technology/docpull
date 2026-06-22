"""Authenticated-source CLI tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from docpull import auth_cli


class _FakeAuthFetcher:
    def __init__(self, config: Any) -> None:
        self.config = config

    async def __aenter__(self) -> _FakeAuthFetcher:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def fetch_one(self, url: str, *, save: bool) -> SimpleNamespace:
        assert save is False
        return SimpleNamespace(
            error=None,
            should_skip=False,
            status_code=204,
            content_type="text/html",
            bytes_downloaded=42,
            skip_reason=None,
        )


def test_auth_kwargs_accepts_one_credential_and_rejects_ambiguous_input() -> None:
    bearer = auth_cli._auth_kwargs(
        auth_policy="explicit-private",
        auth_bearer="bearer-secret",
        auth_basic=None,
        auth_cookie=None,
        auth_header=None,
    )
    assert bearer["type"] == "bearer"
    assert bearer["token"] == "bearer-secret"

    basic = auth_cli._auth_kwargs(
        auth_policy="explicit-private",
        auth_bearer=None,
        auth_basic="user:pass",
        auth_cookie=None,
        auth_header=None,
    )
    assert basic["username"] == "user"
    assert basic["password"] == "pass"

    cookie = auth_cli._auth_kwargs(
        auth_policy="public-token-only",
        auth_bearer=None,
        auth_basic=None,
        auth_cookie="session=abc",
        auth_header=None,
    )
    assert cookie["type"] == "cookie"

    header = auth_cli._auth_kwargs(
        auth_policy="public-token-only",
        auth_bearer=None,
        auth_basic=None,
        auth_cookie=None,
        auth_header=("X-Token", "secret"),
    )
    assert header["header_name"] == "X-Token"

    with pytest.raises(auth_cli.AuthCliError, match="exactly one"):
        auth_cli._auth_kwargs(
            auth_policy="explicit-private",
            auth_bearer="one",
            auth_basic="user:pass",
            auth_cookie=None,
            auth_header=None,
        )
    with pytest.raises(auth_cli.AuthCliError, match="USER:PASS"):
        auth_cli._auth_kwargs(
            auth_policy="explicit-private",
            auth_bearer=None,
            auth_basic="missing-colon",
            auth_cookie=None,
            auth_header=None,
        )


def test_auth_check_returns_non_secret_preflight_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_cli, "Fetcher", _FakeAuthFetcher)

    payload = asyncio.run(
        auth_cli.auth_check(
            "https://docs.example.com/private",
            auth_policy="explicit-private",
            auth_basic="user:pass",
        )
    )

    assert payload["ok"] is True
    assert payload["host"] == "docs.example.com"
    assert payload["auth_type"] == "basic"
    assert payload["status_code"] == 204
    assert "pass" not in json.dumps(payload)


def test_auth_cli_writes_json_without_printing_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fake_auth_check(*_args: Any, **_kwargs: Any) -> dict[str, object]:
        return {
            "schema_version": 1,
            "generated_at": "2026-06-19T00:00:00+00:00",
            "url": "https://docs.example.com/private",
            "host": "docs.example.com",
            "ok": True,
            "auth_policy": "explicit-private",
            "auth_type": "bearer",
            "status_code": 200,
            "content_type": "text/html",
            "bytes_downloaded": 5,
            "skip_reason": None,
            "error": None,
            "secret_handling": "Credential values are never included in this report.",
        }

    monkeypatch.setattr(auth_cli, "auth_check", fake_auth_check)
    output_path = tmp_path / "auth-check.json"

    assert (
        auth_cli.run_auth_cli(
            [
                "check",
                "https://docs.example.com/private",
                "--auth-policy",
                "explicit-private",
                "--auth-bearer",
                "super-secret",
                "--json",
                "--output",
                str(output_path),
            ]
        )
        == 0
    )

    stdout = capsys.readouterr().out
    assert "super-secret" not in stdout
    assert "super-secret" not in output_path.read_text(encoding="utf-8")
    assert json.loads(output_path.read_text(encoding="utf-8"))["ok"] is True


def test_auth_cli_reports_validation_errors_without_tracebacks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert (
        auth_cli.run_auth_cli(
            [
                "check",
                "https://docs.example.com/private",
                "--auth-policy",
                "explicit-private",
            ]
        )
        == 1
    )
    assert "exactly one" in capsys.readouterr().out
