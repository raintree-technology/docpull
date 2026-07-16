"""Regression tests for the documented CLI / SDK / MCP surface contract."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import docpull
import docpull.context_packs as context_packs
from docpull.cli import create_parser
from docpull.cli import main as cli_main
from docpull.mcp.sources import BUILTIN_SOURCES
from docpull.surface import (
    PRUNED_BUILTIN_SOURCE_ALIASES,
    PRUNED_CLI_COMMANDS,
    PRUNED_CONTEXT_PACK_EXPORTS,
    PRUNED_MCP_TOOLS,
    PRUNED_PACK_SUBCOMMANDS,
    PRUNED_PACKAGE_EXTRAS,
    PRUNED_SDK_EXPORTS,
    PUBLIC_CLI_COMMAND_NAMES,
    PUBLIC_CONTEXT_PACK_EXPORTS,
    PUBLIC_MCP_TOOLS,
    PUBLIC_PACK_SUBCOMMANDS,
    PUBLIC_SDK_EXPORTS,
)

ROOT = Path(__file__).resolve().parents[1]
LEGACY_BROWSER_RUNNER = "playwright"
LEGACY_BROWSER_RENDERER = "Playwright"
LEGACY_BROWSER_AVAILABILITY_CHECK = "check_" + LEGACY_BROWSER_RUNNER + "_availability"

PRIMARY_DOC_PATHS = [
    "README.md",
    "docs/examples/README.md",
    "docs/context-packs.md",
    "docs/context-ci.md",
    "docs/context-pack-contract-v3.md",
    "docs/context-dependencies.md",
    "docs/scraping-boundary.md",
    "docs/surface-contract.md",
]


def test_documented_sdk_exports_remain_public() -> None:
    assert tuple(docpull.__all__) == PUBLIC_SDK_EXPORTS
    assert set(docpull.__all__).isdisjoint(PRUNED_SDK_EXPORTS)
    assert f"{LEGACY_BROWSER_RENDERER}Renderer" not in docpull.__all__
    assert LEGACY_BROWSER_AVAILABILITY_CHECK not in docpull.__all__


def test_context_pack_package_exports_match_public_lanes() -> None:
    assert tuple(context_packs.__all__) == PUBLIC_CONTEXT_PACK_EXPORTS
    assert set(context_packs.__all__).isdisjoint(PRUNED_CONTEXT_PACK_EXPORTS)


def test_documented_mcp_tools_remain_registered() -> None:
    server_source = (ROOT / "src/docpull/mcp/server.py").read_text(encoding="utf-8")

    for tool_name in PUBLIC_MCP_TOOLS:
        assert f'name="{tool_name}"' in server_source

    for tool_name in PRUNED_MCP_TOOLS:
        assert f'name="{tool_name}"' not in server_source


def test_pruned_package_extras_remain_private() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    for extra in PRUNED_PACKAGE_EXTRAS:
        assert f"{extra} = [" not in pyproject

    assert LEGACY_BROWSER_RUNNER not in pyproject
    assert "parallel-web" not in pyproject
    assert "raindrop-ai" not in pyproject


def test_provider_specific_aliases_are_not_bundled_sources() -> None:
    assert set(BUILTIN_SOURCES).isdisjoint(PRUNED_BUILTIN_SOURCE_ALIASES)


def test_documented_cli_workflows_remain_dispatched() -> None:
    cli_source = (ROOT / "src/docpull/cli.py").read_text(encoding="utf-8")
    help_text = create_parser().format_help()
    help_lines = help_text.splitlines()
    start = help_lines.index("Subcommands:") + 1
    help_commands: set[str] = set()
    for line in help_lines[start:]:
        if line.startswith("  ") and line.split():
            help_commands.add(line.split()[0])

    for workflow in PUBLIC_CLI_COMMAND_NAMES:
        assert f'"{workflow}"' in cli_source
        assert workflow in help_commands

    for workflow in PRUNED_CLI_COMMANDS:
        assert f'raw_argv[0] == "{workflow}"' not in cli_source
        assert workflow not in help_commands

    assert LEGACY_BROWSER_RUNNER not in cli_source
    assert LEGACY_BROWSER_RENDERER not in cli_source

    args = create_parser().parse_args(["https://example.com"])
    assert args.url == "https://example.com"


def test_pruned_cli_workflows_fail_explicitly(capsys: pytest.CaptureFixture[str]) -> None:
    for command in sorted(PRUNED_CLI_COMMANDS):
        assert _cli_exit_code([command, "--help"]) == 2, command
        captured = capsys.readouterr()
        assert "removed from the public v3 surface" in captured.err


def test_public_cli_workflow_help_is_routable(capsys: pytest.CaptureFixture[str]) -> None:
    for command in PUBLIC_CLI_COMMAND_NAMES:
        assert _cli_exit_code([command, "--help"]) == 0, command

        capsys.readouterr()


def test_pack_help_matches_public_contract() -> None:
    from docpull.pack_tools import create_pack_parser

    parser = create_pack_parser()
    help_text = parser.format_help()
    pack_commands: set[str] = set()
    for action in parser._actions:
        if getattr(action, "dest", None) == "command":
            pack_commands = set(action.choices)
            break

    assert pack_commands == PUBLIC_PACK_SUBCOMMANDS
    for command in PRUNED_PACK_SUBCOMMANDS:
        assert command not in help_text


def test_docs_link_to_surface_contract() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    plugin_readme = (ROOT / "plugin/README.md").read_text(encoding="utf-8")

    assert "docs/surface-contract.md" in readme
    assert "docs/surface-contract.md" in plugin_readme


def test_primary_docs_do_not_publish_pruned_surface() -> None:
    pruned_command_re = re.compile(
        r"\bdocpull\s+("
        + "|".join(re.escape(command) for command in sorted(PRUNED_CLI_COMMANDS, key=len, reverse=True))
        + r")\b"
    )

    for relative_path in PRIMARY_DOC_PATHS:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        assert f"docpull[{LEGACY_BROWSER_RUNNER}]" not in text
        assert "docpull[all]" not in text
        assert f"--runtime {LEGACY_BROWSER_RUNNER}" not in text
        assert f"--render-runtime {LEGACY_BROWSER_RUNNER}" not in text
        assert not pruned_command_re.search(text), relative_path

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Firecrawl" not in readme
    assert "Tavily" not in readme
    assert "Crawl4AI" not in readme

    context_dependencies = (ROOT / "docs/context-dependencies.md").read_text(encoding="utf-8")
    assert "Context Pack Contract v2" not in context_dependencies


def test_surface_contract_states_non_1_to_1_policy() -> None:
    contract = (ROOT / "docs/surface-contract.md").read_text(encoding="utf-8")

    assert "DocPull aligns core workflows across CLI, Python SDK, and MCP" in contract
    assert "API** means the Python SDK / library API" in contract
    assert "Hosted HTTP API" not in contract
    assert "hosted-api.md" not in contract
    assert "Core-aligned" in contract
    assert "Adapted" in contract
    assert "Surface-specific" in contract
    assert "not 1:1 flag parity" in contract


def _cli_exit_code(argv: list[str]) -> int:
    try:
        return cli_main(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
