"""Local source graph tests."""

from __future__ import annotations

import json
from pathlib import Path

from docpull.cli import main
from docpull.graph import build_graph, graph_neighbors, graph_status, load_graph, query_graph, refresh_graph
from tests.pack_fixtures import write_context_pack


def _graph_records(pack_dir: Path, name: str) -> list[dict[str, object]]:
    path = pack_dir / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _records() -> list[dict[str, object]]:
    return [
        {
            "document_id": "doc_search",
            "chunk_id": "chunk_search_1",
            "url": "https://docs.parallel.ai/api-reference/search/search",
            "title": "Parallel Search API",
            "content": (
                "Parallel Search API version 1.2.3 returns cited JSON results for live "
                "agent search. Contact support@example.com for access."
            ),
            "content_hash": "hash_search_1",
            "source_type": "parallel_extract",
            "chunk_index": 0,
            "chunk_heading": "Search",
            "token_count": 19,
        },
        {
            "document_id": "doc_extract",
            "chunk_id": "chunk_extract_1",
            "url": "https://docs.parallel.ai/api-reference/extract/extract",
            "title": "Parallel Extract API",
            "content": "Parallel Extract API turns known URLs into markdown context packs.",
            "content_hash": "hash_extract_1",
            "source_type": "parallel_extract",
            "chunk_index": 0,
            "chunk_heading": "Extract",
            "token_count": 10,
        },
    ]


def test_build_graph_writes_nodes_edges_and_provenance(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir, records=_records())

    payload = build_graph(pack_dir)

    assert payload["status"] == "current"
    assert payload["summary"]["source_count"] == 2
    assert payload["summary"]["chunk_count"] == 2
    assert payload["summary"]["entity_count"] >= 2
    assert (pack_dir / "graph.json").exists()
    assert (pack_dir / "graph.nodes.ndjson").exists()
    assert (pack_dir / "graph.edges.ndjson").exists()
    assert (pack_dir / "GRAPH.md").exists()

    loaded = load_graph(pack_dir)
    nodes = loaded["nodes"]
    edges = loaded["edges"]
    assert any(node["type"] == "source" and node["citation_id"] == "S1" for node in nodes)
    assert any(node["type"] == "entity" and node["normalized"] == "support@example.com" for node in nodes)

    chunk_entity_edges = [edge for edge in edges if edge["type"] == "chunk_entity"]
    assert chunk_entity_edges
    edge = chunk_entity_edges[0]
    for key in ("url", "citation_id", "document_id", "chunk_id", "content_hash", "title", "excerpt"):
        assert edge[key]
    relation_edges = [edge for edge in edges if edge["type"] == "entity_relation"]
    assert relation_edges
    assert any(edge["relationship"] == "returns" for edge in relation_edges)


def test_graph_query_and_neighbors_return_cited_evidence(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir, records=_records())
    build_graph(pack_dir)

    query = query_graph(pack_dir, "support@example.com", limit=5)
    assert query["result_count"] >= 1
    assert query["results"][0]["citation_id"].startswith("S")
    assert query["citations"]

    neighbors = graph_neighbors(pack_dir, "support@example.com", limit=10)
    assert neighbors["matched_entity_count"] == 1
    assert neighbors["neighbor_count"] >= 1
    assert any(item["type"] == "chunk" for item in neighbors["neighbors"])


def test_graph_status_detects_stale_pack_and_refresh_writes_diff(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    records = write_context_pack(pack_dir, records=_records())
    build_graph(pack_dir)

    records[0]["content_hash"] = "hash_search_2"
    (pack_dir / "documents.ndjson").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )

    status = graph_status(pack_dir)
    assert status["status"] == "stale"
    assert "hash_search_2" in status["diff"]["content_hashes_added"]

    refreshed = refresh_graph(pack_dir)
    assert refreshed["new_status"] == "current"
    assert (pack_dir / "graph.diff.json").exists()
    assert graph_status(pack_dir)["status"] == "current"


def test_graph_cli_commands(tmp_path: Path) -> None:
    pack_dir = tmp_path / "pack"
    write_context_pack(pack_dir, records=_records())

    assert main(["graph", "build", str(pack_dir)]) == 0
    assert main(["graph", "status", str(pack_dir)]) == 0
    assert main(["graph", "query", str(pack_dir), "Search API"]) == 0
    assert main(["graph", "neighbors", str(pack_dir), "support@example.com"]) == 0
    assert main(["graph", "refresh", str(pack_dir)]) == 0
    assert main(["graph", "build", str(tmp_path / "missing")]) == 1
