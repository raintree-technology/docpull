"""Lightweight registry of supported local pack export formats."""

from __future__ import annotations

JSONL_FORMATS = {
    "openai-vector-jsonl",
    "langchain-jsonl",
    "llamaindex-jsonl",
    "dspy-jsonl",
}
AGENT_FORMATS = {
    "codex-skill",
    "claude-skill",
    "cursor-rules",
}
TABLE_FORMATS = {
    "sheets-csv",
    "sheets-tsv",
    "warehouse-ndjson",
    "parquet",
}
DOWNSTREAM_JSON_FORMATS = {
    "n8n-json",
    "vercel-ai-json",
    "crewai-json",
}
EXPORT_FORMATS = tuple(sorted(JSONL_FORMATS | AGENT_FORMATS | TABLE_FORMATS | DOWNSTREAM_JSON_FORMATS))
