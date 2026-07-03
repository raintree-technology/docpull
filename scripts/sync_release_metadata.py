#!/usr/bin/env python3
"""Synchronize generated release metadata from source-of-truth files."""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
MCP_SERVER = ROOT / "src" / "docpull" / "mcp" / "server.py"
PLUGIN_README = ROOT / "plugin" / "README.md"
PLUGIN_MANIFESTS = (
    ROOT / "plugin" / ".codex-plugin" / "plugin.json",
    ROOT / "plugin" / ".claude-plugin" / "plugin.json",
)

SECTION_RE = re.compile(r"^\[[^\]]+]")
PROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"(?:\s*#.*)?$')
MCP_TOOLS_START = "<!-- docpull:mcp-tools:start -->"
MCP_TOOLS_END = "<!-- docpull:mcp-tools:end -->"
MCP_TOOLS_BLOCK_RE = re.compile(
    rf"{re.escape(MCP_TOOLS_START)}\n.*?\n{re.escape(MCP_TOOLS_END)}",
    re.DOTALL,
)
VERSION_HINT_RE = re.compile(r"(docpull --version\s+# should print )[^\n]+")


@dataclass(frozen=True)
class McpTool:
    name: str
    read_only: bool


def project_version() -> str:
    in_project = False
    for line in PYPROJECT.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped == "[project]":
            in_project = True
            continue
        if in_project and SECTION_RE.match(stripped):
            break
        if in_project:
            match = PROJECT_VERSION_RE.match(stripped)
            if match:
                return match.group(1)
    raise RuntimeError("Could not find [project].version in pyproject.toml")


def call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def constant_bool(node: ast.expr) -> bool | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def tool_annotations_read_only(node: ast.expr) -> bool | None:
    if not isinstance(node, ast.Call) or call_name(node.func) != "ToolAnnotations":
        return None
    for keyword in node.keywords:
        if keyword.arg == "readOnlyHint":
            return constant_bool(keyword.value)
    return None


def mcp_tools() -> list[McpTool]:
    tree = ast.parse(MCP_SERVER.read_text(encoding="utf-8"), filename=str(MCP_SERVER))
    discovered: list[tuple[int, McpTool]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or call_name(node.func) != "Tool":
            continue

        name: str | None = None
        read_only: bool | None = None
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                if isinstance(keyword.value.value, str):
                    name = keyword.value.value
            elif keyword.arg == "annotations":
                read_only = tool_annotations_read_only(keyword.value)

        if name is None:
            raise RuntimeError(f"Tool declaration at line {node.lineno} is missing a literal name")
        if read_only is None:
            raise RuntimeError(f"Tool {name!r} at line {node.lineno} is missing readOnlyHint")
        discovered.append((node.lineno, McpTool(name=name, read_only=read_only)))

    tools = [tool for _, tool in sorted(discovered, key=lambda item: item[0])]
    names = [tool.name for tool in tools]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise RuntimeError(f"Duplicate MCP tool names: {', '.join(duplicates)}")
    if not tools:
        raise RuntimeError("No MCP tools found in src/docpull/mcp/server.py")
    return tools


def code_list(names: list[str]) -> str:
    return ", ".join(f"`{name}`" for name in names)


def expected_mcp_tools_block(tools: list[McpTool]) -> str:
    read_tools = [tool.name for tool in tools if tool.read_only]
    write_tools = [tool.name for tool in tools if not tool.read_only]
    if not read_tools or not write_tools:
        raise RuntimeError("Expected both read and write MCP tools")

    return "\n".join(
        [
            MCP_TOOLS_START,
            f"- **MCP server** ({len(tools)} tools):",
            f"  - Read: {code_list(read_tools)}",
            f"  - Write: {code_list(write_tools)}",
            (
                "  - All read tools advertise `readOnlyHint` so hosts that auto-approve "
                "safe tools won't prompt for them."
            ),
            MCP_TOOLS_END,
        ]
    )


def expected_readme(current: str, version: str, tools: list[McpTool]) -> str:
    readme, tool_replacements = MCP_TOOLS_BLOCK_RE.subn(expected_mcp_tools_block(tools), current)
    if tool_replacements != 1:
        raise RuntimeError("plugin/README.md must contain exactly one generated MCP tools block")

    readme, version_replacements = VERSION_HINT_RE.subn(
        rf"\g<1>{version} or newer",
        readme,
    )
    if version_replacements != 1:
        raise RuntimeError("plugin/README.md must contain exactly one docpull --version hint")
    return readme


def expected_manifest(current: str, version: str) -> str:
    manifest = json.loads(current)
    manifest["version"] = version
    return f"{json.dumps(manifest, indent=2)}\n"


def expected_files() -> dict[Path, str]:
    version = project_version()
    tools = mcp_tools()
    files = {
        PLUGIN_README: expected_readme(PLUGIN_README.read_text(encoding="utf-8"), version, tools),
    }
    for manifest_path in PLUGIN_MANIFESTS:
        files[manifest_path] = expected_manifest(manifest_path.read_text(encoding="utf-8"), version)
    return files


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def check() -> int:
    drifted: list[str] = []
    for source_path, expected in expected_files().items():
        if source_path.read_text(encoding="utf-8") != expected:
            drifted.append(relative(source_path))

    if drifted:
        print("Generated release metadata is stale:", file=sys.stderr)
        for drifted_path in drifted:
            print(f"  - {drifted_path}", file=sys.stderr)
        print("Run `make metadata-sync` and commit the result.", file=sys.stderr)
        return 1

    version = project_version()
    tools = mcp_tools()
    print(f"Release metadata is synchronized for {version} ({len(tools)} MCP tools).")
    return 0


def write() -> int:
    changed: list[str] = []
    for source_path, expected in expected_files().items():
        if source_path.read_text(encoding="utf-8") != expected:
            source_path.write_text(expected, encoding="utf-8")
            changed.append(relative(source_path))

    if changed:
        print("Updated generated release metadata:")
        for changed_path in changed:
            print(f"  - {changed_path}")
    else:
        print("Generated release metadata already synchronized.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Fail if generated metadata is stale")
    mode.add_argument("--write", action="store_true", help="Rewrite generated metadata")
    args = parser.parse_args(argv)

    if args.check:
        return check()
    return write()


if __name__ == "__main__":
    raise SystemExit(main())
