"""Agent-readable publishing artifacts for DocPull packs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .pack_reader import PackReadError, load_pack
from .pack_tools import build_citation_map, score_pack, score_pack_sources
from .time_utils import utc_now_iso

PUBLISH_SCHEMA_VERSION = 1


class AgentPublishError(RuntimeError):
    """Raised when agent publishing cannot complete."""


def publish_agent_docs(pack_dir: Path | str, *, target: str = "agent-docs") -> dict[str, Any]:
    """Write agent-facing load docs for a local pack."""

    if target != "agent-docs":
        raise AgentPublishError("Only --target agent-docs is supported")
    root = Path(pack_dir).expanduser().resolve()
    try:
        pack = load_pack(root)
    except PackReadError as err:
        raise AgentPublishError(str(err)) from err

    citations = build_citation_map(root)
    score = score_pack(root)
    source_scores = score_pack_sources(root)
    token_estimate = sum(int(getattr(record, "token_count", 0) or 0) for record in pack.documents)
    payload = {
        "schema_version": PUBLISH_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "target": target,
        "pack_dir": str(root),
        "document_count": len(pack.documents),
        "source_count": len(pack.sources),
        "token_estimate": token_estimate,
        "pack_score": score.get("score"),
        "pack_grade": score.get("grade"),
        "artifacts": {
            "agent_context": "AGENT_CONTEXT.md",
            "llms": "llms.txt",
            "llms_full": "llms-full.txt",
            "mcp_snippets": "MCP_SNIPPETS.md",
            "install": "INSTALL.md",
            "source_index": "SOURCE_INDEX.md",
            "basis": "basis.ndjson",
            "basis_report": "basis.report.json",
            "basis_markdown": "BASIS.md",
            "publish": "agent.publish.json",
        },
    }
    _write_json(root / "agent.publish.json", payload)
    (root / "AGENT_CONTEXT.md").write_text(
        _agent_context_markdown(root, payload, citations, source_scores),
        encoding="utf-8",
    )
    (root / "llms.txt").write_text(_llms_index(citations), encoding="utf-8")
    (root / "llms-full.txt").write_text(_llms_full(pack), encoding="utf-8")
    (root / "MCP_SNIPPETS.md").write_text(_mcp_snippets(root), encoding="utf-8")
    (root / "INSTALL.md").write_text(_install_markdown(root), encoding="utf-8")
    (root / "SOURCE_INDEX.md").write_text(_source_index_markdown(citations, source_scores), encoding="utf-8")
    return payload


def _agent_context_markdown(
    root: Path,
    payload: dict[str, Any],
    citations: dict[str, Any],
    source_scores: dict[str, Any],
) -> str:
    lines = [
        "# Agent Context",
        "",
        f"Pack: `{root}`",
        f"Sources: {payload['source_count']}",
        f"Documents: {payload['document_count']}",
        f"Token estimate: {payload['token_estimate']}",
        f"Pack score: {payload.get('pack_score')} ({payload.get('pack_grade')})",
        "",
        "## How To Use",
        "",
        f'- Search: `docpull pack search {root} "query"`',
        f'- Answer: `docpull answer-pack {root} "question"`',
        f"- Cite: `docpull pack citations {root}`",
        f"- Audit: `docpull pack audit {root}`",
        f"- Refresh: `docpull refresh {root}`",
        f"- Export: `docpull export {root} --format openai-vector-jsonl -o openai-vector.jsonl`",
        "",
        "## Citation Rules",
        "",
        "Use citation IDs from `citations.json` or `SOURCE_INDEX.md` when making claims.",
        "Use `basis.ndjson` and `BASIS.md` to check whether a claim is supported, partial, or insufficient.",
        (
            "If the basis is partial or insufficient, refuse the claim or ask for fresher "
            "context instead of guessing."
        ),
        "",
        "## Source Load Order",
        "",
    ]
    scores = source_scores.get("sources", []) if isinstance(source_scores, dict) else []
    if scores:
        for item in scores[:25]:
            lines.append(f"- {item.get('grade', 'source')}: {item.get('url')}")
    else:
        for source in citations.get("sources", [])[:25]:
            lines.append(f"- [{source.get('citation_id')}] {source.get('url')}")
    lines.extend(["", "## Artifacts", ""])
    for name, rel in payload["artifacts"].items():
        lines.append(f"- `{name}`: `{rel}`")
    return "\n".join(lines).rstrip() + "\n"


def _llms_index(citations: dict[str, Any]) -> str:
    lines = ["# DocPull Context Pack", "", "> Agent-readable source index.", ""]
    for source in citations.get("sources", []):
        lines.append(f"- [{source.get('title') or source.get('url')}]({source.get('url')})")
    return "\n".join(lines).rstrip() + "\n"


def _llms_full(pack: Any) -> str:
    lines = ["# DocPull Context Pack Full Text", ""]
    for record in pack.documents:
        title = getattr(record, "title", None) or getattr(record, "url", "")
        url = getattr(record, "url", "")
        content = str(getattr(record, "content", "") or "").strip()
        lines.extend([f"## {title}", "", f"Source: {url}", "", content, ""])
    return "\n".join(lines).rstrip() + "\n"


def _mcp_snippets(root: Path) -> str:
    return (
        "# MCP Snippets\n\n"
        "Use DocPull's local MCP server, then call pack tools against this directory.\n\n"
        "```json\n"
        + json.dumps(
            {
                "mcpServers": {
                    "docpull": {
                        "command": "docpull",
                        "args": ["mcp"],
                    }
                }
            },
            indent=2,
        )
        + "\n```\n\n"
        f"Suggested pack path: `{root}`\n"
    )


def _install_markdown(root: Path) -> str:
    return (
        "# Install\n\n"
        "```bash\n"
        "pip install docpull\n"
        f"docpull pack prepare {root}\n"
        f"docpull pack publish {root} --target agent-docs\n"
        "```\n"
    )


def _source_index_markdown(citations: dict[str, Any], source_scores: dict[str, Any]) -> str:
    lines = ["# Source Index", ""]
    score_by_url = {
        str(item.get("url")): item
        for item in source_scores.get("sources", [])
        if isinstance(item, dict) and item.get("url")
    }
    for source in citations.get("sources", []):
        url = str(source.get("url") or "")
        score = score_by_url.get(url, {})
        suffix = f" - {score.get('grade')} score={score.get('score')}" if score else ""
        lines.append(f"- [{source.get('citation_id')}] {source.get('title') or url} - {url}{suffix}")
    return "\n".join(lines).rstrip() + "\n"


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
