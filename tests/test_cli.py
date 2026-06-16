"""CLI regression tests."""

from importlib.metadata import version
from types import SimpleNamespace

import pytest

import docpull
from docpull.cli import create_parser, main, run_fetcher
from docpull.models.events import SkipReason


def test_runtime_version_matches_package_metadata():
    assert docpull.__version__ == version("docpull")


def test_parser_rejects_removed_js_flag():
    """Ensure the removed JavaScript flag stays unavailable."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["https://example.com", "--js"])


@pytest.mark.parametrize("alias", ["flat", "short"])
def test_parser_rejects_removed_naming_aliases(alias: str):
    """Ensure removed naming aliases stay unavailable at the CLI boundary."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["https://example.com", "--naming-strategy", alias])


def test_parser_accepts_supported_naming_strategies():
    parser = create_parser()

    full = parser.parse_args(["https://example.com", "--naming-strategy", "full"])
    hierarchical = parser.parse_args(["https://example.com", "--naming-strategy", "hierarchical"])

    assert full.naming_strategy == "full"
    assert hierarchical.naming_strategy == "hierarchical"


def test_parser_accepts_okf_profile_and_format():
    parser = create_parser()

    profile = parser.parse_args(["https://example.com", "--profile", "okf"])
    output_format = parser.parse_args(["https://example.com", "--format", "okf"])

    assert profile.profile == "okf"
    assert output_format.format == "okf"


def test_parser_accepts_sec_filing_profile():
    parser = create_parser()

    args = parser.parse_args(["https://www.sec.gov/Archives/example.htm", "--profile", "sec-filing"])

    assert args.profile == "sec-filing"


def test_evidence_pack_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as exc_info:
        main(["evidence-pack", "--help"])

    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "docpull evidence-pack" in captured.out
    assert "--sec-user-agent" in captured.out


def test_evidence_pack_dispatch_reports_user_error(capsys):
    result = main(["evidence-pack", "missing.ndjson", "--rules", "missing.yml"])

    assert result == 1
    captured = capsys.readouterr()
    assert "Evidence pack error:" in captured.out
    assert "Input filing NDJSON does not exist" in captured.out


def test_skill_rejects_okf_output(tmp_path, capsys):
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--skill",
            "my-docs",
            "--format",
            "okf",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert run_fetcher(args) == 1
    captured = capsys.readouterr()
    assert "--skill cannot be combined with OKF output" in captured.out


def test_parser_accepts_per_host_concurrency():
    parser = create_parser()

    args = parser.parse_args(["https://example.com", "--max-concurrent", "50", "--per-host-concurrent", "10"])

    assert args.max_concurrent == 50
    assert args.per_host_concurrent == 10


def test_help_describes_insecure_tls_as_rejected():
    parser = create_parser()

    assert "Deprecated and rejected" in parser.format_help()


def test_help_describes_mirror_naming_override():
    parser = create_parser()
    help_text = " ".join(parser.format_help().split())

    assert "Mirror profile defaults to hierarchical unless explicitly overridden" in help_text


def test_single_invalid_url_returns_nonzero(tmp_path):
    parser = create_parser()
    args = parser.parse_args(["http://example.com", "--single", "--output-dir", str(tmp_path)])

    assert run_fetcher(args) == 1


def test_configuration_errors_escape_rich_markup(tmp_path, capsys):
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--single",
            "--skill",
            "BadName",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert run_fetcher(args) == 1
    captured = capsys.readouterr()
    assert r"^[a-z0-9][a-z0-9-]*$" in captured.out


def test_single_no_content_skip_returns_nonzero(tmp_path, monkeypatch):
    class FakeFetcher:
        stats = SimpleNamespace()

        def __init__(self, config):
            self.config = config

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def fetch_one(self, url):
            return SimpleNamespace(
                error=None,
                should_skip=True,
                skip_reason="No content extracted",
                skip_code=SkipReason.NO_CONTENT_EXTRACTED,
            )

    monkeypatch.setattr("docpull.cli.Fetcher", FakeFetcher)
    parser = create_parser()
    args = parser.parse_args(["https://example.com/empty", "--single", "--output-dir", str(tmp_path)])

    assert run_fetcher(args) == 1
