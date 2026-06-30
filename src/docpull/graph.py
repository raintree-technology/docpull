"""Local source graph artifacts for DocPull context packs."""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markup import escape

from .pack_reader import LocalPack, PackReadError, load_pack
from .pack_tools import PackToolError, extract_pack_entities
from .time_utils import utc_now_iso

GRAPH_SCHEMA_VERSION = 1
DEFAULT_ENTITY_LIMIT = 500
DEFAULT_QUERY_LIMIT = 10
DEFAULT_NEIGHBOR_LIMIT = 20
MAX_GRAPH_ENTITIES_PER_CHUNK = 24
MAX_GRAPH_ENTITIES_PER_SENTENCE = 8

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,}", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_RELATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("supports", re.compile(r"\bsupports?\b", re.IGNORECASE)),
    ("returns", re.compile(r"\breturns?\b", re.IGNORECASE)),
    ("requires", re.compile(r"\brequires?\b", re.IGNORECASE)),
    ("uses", re.compile(r"\buses?\b", re.IGNORECASE)),
    ("provides", re.compile(r"\bprovides?\b", re.IGNORECASE)),
    ("turns_into", re.compile(r"\bturns?\b.+\binto\b", re.IGNORECASE)),
    ("documents", re.compile(r"\bdocuments?|documented\b", re.IGNORECASE)),
    ("contacts", re.compile(r"\bcontacts?\b", re.IGNORECASE)),
)


class GraphError(RuntimeError):
    """User-facing graph workflow error."""


def create_graph_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull graph",
        description="Build and query local cited source graphs for DocPull packs",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build local graph artifacts for a pack")
    build.add_argument("pack_dir", type=Path)
    build.add_argument("--entity-limit", type=int, default=DEFAULT_ENTITY_LIMIT)

    status = subparsers.add_parser("status", help="Report whether graph artifacts match the pack")
    status.add_argument("pack_dir", type=Path)

    query = subparsers.add_parser("query", help="Search graph nodes and cited edge evidence")
    query.add_argument("pack_dir", type=Path)
    query.add_argument("query")
    query.add_argument("--limit", type=int, default=DEFAULT_QUERY_LIMIT)

    neighbors = subparsers.add_parser("neighbors", help="List cited neighbors for a graph entity")
    neighbors.add_argument("pack_dir", type=Path)
    neighbors.add_argument("entity")
    neighbors.add_argument("--limit", type=int, default=DEFAULT_NEIGHBOR_LIMIT)

    refresh = subparsers.add_parser("refresh", help="Rebuild graph artifacts and write graph.diff.json")
    refresh.add_argument("pack_dir", type=Path)
    refresh.add_argument("--entity-limit", type=int, default=DEFAULT_ENTITY_LIMIT)

    return parser


def run_graph_cli(argv: list[str] | None = None) -> int:
    parser = create_graph_parser()
    args = parser.parse_args(argv)
    console = Console()
    try:
        if args.command == "build":
            payload = build_graph(args.pack_dir, entity_limit=args.entity_limit)
            console.print(
                "[green]Graph built:[/green] "
                f"{payload['summary']['node_count']} nodes, "
                f"{payload['summary']['edge_count']} edges -> {payload['artifacts']['graph']}"
            )
            return 0
        if args.command == "status":
            payload = graph_status(args.pack_dir)
            status = payload["status"]
            console.print(f"[green]Graph status:[/green] {status}")
            return 0 if status in {"current", "missing"} else 2
        if args.command == "query":
            payload = query_graph(args.pack_dir, args.query, limit=args.limit)
            console.print(f"[green]Graph query:[/green] {payload['result_count']} results")
            for result in payload["results"][: args.limit]:
                citation = result.get("citation_id") or "-"
                console.print(
                    f"{result['rank']}. [{citation}] {escape(str(result['label']))} "
                    f"({escape(str(result['kind']))})"
                )
            return 0
        if args.command == "neighbors":
            payload = graph_neighbors(args.pack_dir, args.entity, limit=args.limit)
            console.print(f"[green]Graph neighbors:[/green] {payload['neighbor_count']} results")
            for result in payload["neighbors"][: args.limit]:
                citation = result.get("citation_id") or "-"
                console.print(
                    f"{result['rank']}. [{citation}] {escape(str(result['label']))} "
                    f"via {escape(str(result['relationship']))}"
                )
            return 0
        if args.command == "refresh":
            payload = refresh_graph(args.pack_dir, entity_limit=args.entity_limit)
            summary = payload["summary"]
            console.print(
                "[green]Graph refreshed:[/green] "
                f"+{summary['added_node_count']} nodes "
                f"-{summary['removed_node_count']} nodes "
                f"+{summary['added_edge_count']} edges "
                f"-{summary['removed_edge_count']} edges -> {payload['artifacts']['diff']}"
            )
            return 0
        parser.error(f"Unknown command: {args.command}")
    except (GraphError, PackReadError, PackToolError) as err:
        console.print("[red]Graph error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Graph command failed:[/red] " + escape(str(err)))
        return 1
    return 1


def build_graph(
    pack_dir: Path | str,
    *,
    entity_limit: int = DEFAULT_ENTITY_LIMIT,
    markdown: bool = True,
) -> dict[str, Any]:
    """Build local graph sidecars for a DocPull pack."""
    if entity_limit < 1:
        raise GraphError("entity_limit must be at least 1")

    pack = load_pack(pack_dir)
    if not pack.documents:
        raise GraphError("Cannot build a graph for an empty pack.")

    entities_payload = extract_pack_entities(pack.pack_dir, limit=entity_limit)
    fingerprint = _pack_fingerprint(pack)
    nodes, edges = _build_nodes_and_edges(pack, entities_payload)
    node_path = pack.pack_dir / "graph.nodes.ndjson"
    edge_path = pack.pack_dir / "graph.edges.ndjson"
    graph_path = pack.pack_dir / "graph.json"
    markdown_path = pack.pack_dir / "GRAPH.md"

    _write_ndjson(node_path, nodes)
    _write_ndjson(edge_path, edges)

    entity_nodes = [node for node in nodes if node.get("type") == "entity"]
    edge_types = Counter(str(edge.get("type") or "unknown") for edge in edges)
    artifacts = {
        "graph": _artifact_ref(pack.pack_dir, graph_path),
        "nodes": _artifact_ref(pack.pack_dir, node_path),
        "edges": _artifact_ref(pack.pack_dir, edge_path),
    }
    if markdown:
        artifacts["markdown"] = _artifact_ref(pack.pack_dir, markdown_path)
    payload = {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack.pack_dir),
        "status": "current",
        "pack_fingerprint": fingerprint,
        "summary": {
            "source_count": len(pack.sources),
            "document_count": len({record.document_id for record in pack.documents}),
            "chunk_count": len(pack.documents),
            "entity_count": len(entity_nodes),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "edge_types": dict(edge_types),
        },
        "top_entities": _top_entities(entity_nodes, limit=20),
        "artifacts": artifacts,
    }
    _write_json(graph_path, payload)
    if markdown:
        markdown_path.write_text(_graph_markdown(payload), encoding="utf-8")
    return payload


def load_graph(pack_dir: Path | str) -> dict[str, Any]:
    """Load graph metadata, nodes, and edges from a pack directory."""
    root = Path(pack_dir).expanduser().resolve()
    graph_path = root / "graph.json"
    if not graph_path.exists():
        raise GraphError(f"Missing graph artifacts in {root}. Run `docpull graph build {root}`.")
    graph = _read_json(graph_path)
    nodes = _read_ndjson(root / "graph.nodes.ndjson")
    edges = _read_ndjson(root / "graph.edges.ndjson")
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(root),
        "graph": graph,
        "nodes": nodes,
        "edges": edges,
    }


def graph_status(pack_dir: Path | str) -> dict[str, Any]:
    """Report whether graph artifacts are present and current for a pack."""
    pack = load_pack(pack_dir)
    current = _pack_fingerprint(pack)
    graph_path = pack.pack_dir / "graph.json"
    if not graph_path.exists():
        return {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "pack_dir": str(pack.pack_dir),
            "status": "missing",
            "reason": "graph.json is missing",
            "current_fingerprint": current,
            "graph_fingerprint": None,
        }
    graph = _read_json(graph_path)
    graph_fingerprint = graph.get("pack_fingerprint") if isinstance(graph, dict) else None
    graph_sha = graph_fingerprint.get("sha256") if isinstance(graph_fingerprint, dict) else None
    status = "current" if graph_sha == current["sha256"] else "stale"
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(pack.pack_dir),
        "status": status,
        "current_fingerprint": current,
        "graph_fingerprint": graph_fingerprint,
        "diff": _fingerprint_diff(graph_fingerprint, current),
    }


def query_graph(
    pack_dir: Path | str,
    query: str,
    *,
    limit: int = DEFAULT_QUERY_LIMIT,
) -> dict[str, Any]:
    """Search graph nodes and edge evidence without generating an answer."""
    if not query.strip():
        raise GraphError("query must be non-empty")
    if limit < 1:
        raise GraphError("limit must be at least 1")

    loaded = load_graph(pack_dir)
    nodes = loaded["nodes"]
    edges = loaded["edges"]
    node_by_id = _node_by_id(nodes)
    terms = _keywords(query)
    phrase = _clean_text(query).casefold()
    scored: list[dict[str, Any]] = []

    for node in nodes:
        score, matched = _score_text(_node_search_text(node), terms, phrase)
        if score <= 0:
            continue
        scored.append(_query_result("node", node, score, matched, node_by_id=node_by_id))

    for edge in edges:
        score, matched = _score_text(_edge_search_text(edge), terms, phrase)
        if score <= 0:
            continue
        scored.append(_query_result("edge", edge, score, matched, node_by_id=node_by_id))

    scored.sort(
        key=lambda item: (
            -_safe_int(item.get("score")),
            str(item.get("citation_id") or ""),
            str(item.get("id") or ""),
        )
    )
    results = [{**item, "rank": rank} for rank, item in enumerate(scored[:limit], start=1)]
    citations = _citations_for_results(results, nodes)
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": loaded["pack_dir"],
        "query": query,
        "status": graph_status(pack_dir)["status"],
        "result_count": len(results),
        "results": results,
        "citations": citations,
    }


def graph_neighbors(
    pack_dir: Path | str,
    entity: str,
    *,
    limit: int = DEFAULT_NEIGHBOR_LIMIT,
) -> dict[str, Any]:
    """Return cited neighboring nodes for matching entity nodes."""
    if not entity.strip():
        raise GraphError("entity must be non-empty")
    if limit < 1:
        raise GraphError("limit must be at least 1")

    loaded = load_graph(pack_dir)
    nodes = loaded["nodes"]
    edges = loaded["edges"]
    node_by_id = _node_by_id(nodes)
    terms = _keywords(entity)
    phrase = _clean_text(entity).casefold()
    matches = [
        node
        for node in nodes
        if node.get("type") == "entity" and _score_text(_node_search_text(node), terms, phrase)[0] > 0
    ]
    match_ids = {str(node.get("id")) for node in matches}
    neighbors: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for edge in edges:
        source_id = str(edge.get("from") or "")
        target_id = str(edge.get("to") or "")
        if source_id not in match_ids and target_id not in match_ids:
            continue
        neighbor_id = target_id if source_id in match_ids else source_id
        neighbor = node_by_id.get(neighbor_id)
        if not neighbor:
            continue
        key = (str(edge.get("id") or ""), neighbor_id)
        if key in seen:
            continue
        seen.add(key)
        neighbors.append(
            {
                "id": neighbor_id,
                "label": _node_label(neighbor),
                "type": neighbor.get("type"),
                "relationship": edge.get("relationship"),
                "edge_id": edge.get("id"),
                "citation_id": edge.get("citation_id"),
                "url": edge.get("url"),
                "title": edge.get("title"),
                "document_id": edge.get("document_id"),
                "chunk_id": edge.get("chunk_id"),
                "content_hash": edge.get("content_hash"),
                "excerpt": edge.get("excerpt"),
            }
        )

    neighbors.sort(
        key=lambda item: (
            str(item.get("type") or ""),
            str(item.get("label") or ""),
            str(item.get("edge_id") or ""),
        )
    )
    limited = [{**item, "rank": rank} for rank, item in enumerate(neighbors[:limit], start=1)]
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": loaded["pack_dir"],
        "entity": entity,
        "status": graph_status(pack_dir)["status"],
        "matched_entity_count": len(matches),
        "neighbor_count": len(limited),
        "matched_entities": [_public_node(node) for node in matches],
        "neighbors": limited,
        "citations": _citations_for_results(limited, nodes),
    }


def refresh_graph(
    pack_dir: Path | str,
    *,
    entity_limit: int = DEFAULT_ENTITY_LIMIT,
) -> dict[str, Any]:
    """Rebuild graph artifacts from the current pack and write graph.diff.json."""
    root = Path(pack_dir).expanduser().resolve()
    old_nodes: list[dict[str, Any]] = []
    old_edges: list[dict[str, Any]] = []
    old_status = graph_status(root)["status"] if (root / "documents.ndjson").exists() else "missing_pack"
    if (root / "graph.json").exists():
        try:
            old_loaded = load_graph(root)
            old_nodes = old_loaded["nodes"]
            old_edges = old_loaded["edges"]
        except GraphError:
            old_nodes = []
            old_edges = []

    graph_payload = build_graph(root, entity_limit=entity_limit)
    new_loaded = load_graph(root)
    added_nodes, removed_nodes = _id_diff(old_nodes, new_loaded["nodes"])
    added_edges, removed_edges = _id_diff(old_edges, new_loaded["edges"])
    diff_path = root / "graph.diff.json"
    payload = {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(root),
        "old_status": old_status,
        "new_status": "current",
        "summary": {
            "added_node_count": len(added_nodes),
            "removed_node_count": len(removed_nodes),
            "added_edge_count": len(added_edges),
            "removed_edge_count": len(removed_edges),
            "node_count": graph_payload["summary"]["node_count"],
            "edge_count": graph_payload["summary"]["edge_count"],
        },
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "added_edges": added_edges,
        "removed_edges": removed_edges,
        "artifacts": {
            "graph": "graph.json",
            "nodes": "graph.nodes.ndjson",
            "edges": "graph.edges.ndjson",
            "markdown": "GRAPH.md",
            "diff": "graph.diff.json",
        },
    }
    _write_json(diff_path, payload)
    return payload


def _build_nodes_and_edges(
    pack: LocalPack,
    entities_payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    source_node_by_url: dict[str, str] = {}
    chunk_entity_ids: dict[str, list[str]] = {}

    for source in pack.sources:
        node_id = _stable_id("source", source.url)
        source_node_by_url[source.url] = node_id
        nodes.append(
            {
                "schema_version": GRAPH_SCHEMA_VERSION,
                "id": node_id,
                "type": "source",
                "label": source.title or source.url,
                "url": source.url,
                "title": source.title or source.url,
                "citation_id": source.citation_id,
                "domain": _domain(source.url),
                "path": source.path,
                "record_count": source.record_count,
                "document_ids": list(source.document_ids),
                "content_hashes": list(source.content_hashes),
            }
        )

    document_ids: set[str] = set()
    for record in pack.documents:
        citation_id = pack.citation_by_url.get(record.url)
        source_id = source_node_by_url.get(record.url)
        document_id = record.document_id
        if document_id not in document_ids:
            document_ids.add(document_id)
            nodes.append(
                {
                    "schema_version": GRAPH_SCHEMA_VERSION,
                    "id": document_id,
                    "type": "document",
                    "label": record.title or record.url,
                    "url": record.url,
                    "title": record.title or record.url,
                    "citation_id": citation_id,
                    "content_hash": record.content_hash,
                    "source_type": record.source_type,
                    "fetched_at": record.fetched_at,
                }
            )
            if source_id:
                edges.append(
                    _edge(
                        "source_document",
                        source_id,
                        document_id,
                        "contains_document",
                        record=record,
                        citation_id=citation_id,
                    )
                )

        chunk_id = record.chunk_id or _stable_id("chunk", record.document_id, record.content_hash)
        nodes.append(
            {
                "schema_version": GRAPH_SCHEMA_VERSION,
                "id": chunk_id,
                "type": "chunk",
                "label": record.chunk_heading or record.title or record.url,
                "url": record.url,
                "title": record.title or record.url,
                "citation_id": citation_id,
                "document_id": document_id,
                "content_hash": record.content_hash,
                "chunk_index": record.chunk_index,
                "chunk_heading": record.chunk_heading,
                "token_count": record.token_count,
                "excerpt": _excerpt(record.content),
            }
        )
        edges.append(
            _edge(
                "document_chunk",
                document_id,
                chunk_id,
                "contains_chunk",
                record=record,
                citation_id=citation_id,
            )
        )

    entity_nodes = _entity_nodes(entities_payload)
    entity_rank = {str(node.get("id")): index for index, node in enumerate(entity_nodes)}
    nodes.extend(entity_nodes)
    for record in pack.documents:
        chunk_id = record.chunk_id or _stable_id("chunk", record.document_id, record.content_hash)
        citation_id = pack.citation_by_url.get(record.url)
        for entity_node in entity_nodes:
            if not _entity_in_content(entity_node, record.content):
                continue
            entity_id = str(entity_node["id"])
            chunk_entity_ids.setdefault(chunk_id, []).append(entity_id)
            edges.append(
                _edge(
                    "chunk_entity",
                    chunk_id,
                    entity_id,
                    "mentions_entity",
                    record=record,
                    citation_id=citation_id,
                    excerpt=_entity_excerpt(entity_node, record.content),
                )
            )

    for record in pack.documents:
        chunk_id = record.chunk_id or _stable_id("chunk", record.document_id, record.content_hash)
        entity_ids = _limited_entity_ids(
            chunk_entity_ids.get(chunk_id, []),
            entity_rank=entity_rank,
            limit=MAX_GRAPH_ENTITIES_PER_CHUNK,
        )
        if len(entity_ids) < 2:
            continue
        citation_id = pack.citation_by_url.get(record.url)
        chunk_excerpt = _excerpt(record.content)
        for left, right in itertools.combinations(entity_ids, 2):
            edges.append(
                _edge(
                    "entity_cooccurs",
                    left,
                    right,
                    "co_occurs_in_chunk",
                    record=record,
                    citation_id=citation_id,
                    excerpt=chunk_excerpt,
                )
            )
        edges.extend(
            _entity_relation_edges(
                record,
                entity_nodes=entity_nodes,
                entity_ids=entity_ids,
                entity_rank=entity_rank,
                citation_id=citation_id,
            )
        )

    return _dedupe_by_id(nodes), _dedupe_by_id(edges)


def _entity_nodes(entities_payload: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    entities = entities_payload.get("entities")
    if not isinstance(entities, list):
        return nodes
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        entity_type = str(entity.get("type") or "entity")
        normalized = str(entity.get("normalized") or entity.get("value") or "").strip()
        if not normalized:
            continue
        citations = entity.get("citations") if isinstance(entity.get("citations"), list) else []
        first_citation = citations[0] if citations and isinstance(citations[0], dict) else {}
        nodes.append(
            {
                "schema_version": GRAPH_SCHEMA_VERSION,
                "id": _stable_id("entity", entity_type, normalized.casefold()),
                "type": "entity",
                "label": str(entity.get("value") or normalized),
                "entity_type": entity_type,
                "value": str(entity.get("value") or normalized),
                "normalized": normalized,
                "count": _safe_int(entity.get("count")),
                "source_count": _safe_int(entity.get("source_count")),
                "citation_id": first_citation.get("citation_id"),
                "url": first_citation.get("url"),
                "title": first_citation.get("title"),
                "excerpt": first_citation.get("excerpt"),
                "citations": citations,
            }
        )
    return nodes


def _edge(
    edge_type: str,
    source: str,
    target: str,
    relationship: str,
    *,
    record: Any,
    citation_id: str | None,
    excerpt: str | None = None,
) -> dict[str, Any]:
    edge_id = _stable_id(edge_type, source, target, relationship, record.content_hash)
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "id": edge_id,
        "type": edge_type,
        "from": source,
        "to": target,
        "relationship": relationship,
        "url": record.url,
        "citation_id": citation_id,
        "document_id": record.document_id,
        "chunk_id": record.chunk_id or _stable_id("chunk", record.document_id, record.content_hash),
        "content_hash": record.content_hash,
        "title": record.title or record.url,
        "excerpt": excerpt or _excerpt(record.content),
    }


def _entity_relation_edges(
    record: Any,
    *,
    entity_nodes: list[dict[str, Any]],
    entity_ids: list[str],
    entity_rank: dict[str, int],
    citation_id: str | None,
) -> list[dict[str, Any]]:
    entity_nodes_by_id = {str(node["id"]): node for node in entity_nodes if node.get("id") in entity_ids}
    edges: list[dict[str, Any]] = []
    for sentence in _sentences(record.content):
        sentence_entity_ids = _limited_entity_ids(
            (
                entity_id
                for entity_id, entity_node in entity_nodes_by_id.items()
                if _entity_in_content(entity_node, sentence)
            ),
            entity_rank=entity_rank,
            limit=MAX_GRAPH_ENTITIES_PER_SENTENCE,
        )
        if len(sentence_entity_ids) < 2:
            continue
        relationship = _relation_label(sentence)
        for left, right in itertools.combinations(sentence_entity_ids, 2):
            edges.append(
                _edge(
                    "entity_relation",
                    left,
                    right,
                    relationship,
                    record=record,
                    citation_id=citation_id,
                    excerpt=sentence,
                )
            )
    return edges


def _limited_entity_ids(
    entity_ids: Iterable[str],
    *,
    entity_rank: dict[str, int],
    limit: int,
) -> list[str]:
    unique_ids = {entity_id for entity_id in entity_ids if entity_id}
    return sorted(unique_ids, key=lambda entity_id: (entity_rank.get(entity_id, 1_000_000), entity_id))[
        :limit
    ]


def _pack_fingerprint(pack: LocalPack) -> dict[str, Any]:
    manifest = pack.manifest if isinstance(pack.manifest, dict) else {}
    records = [
        {
            "document_id": record.document_id,
            "chunk_id": record.chunk_id,
            "url": record.url,
            "content_hash": record.content_hash,
        }
        for record in pack.documents
    ]
    records.sort(
        key=lambda item: (
            str(item["url"]),
            str(item["document_id"]),
            str(item.get("chunk_id") or ""),
            str(item["content_hash"]),
        )
    )
    manifest_records = manifest.get("records")
    payload = {
        "document_count": len({record["document_id"] for record in records}),
        "record_count": len(records),
        "chunk_count": sum(1 for record in pack.documents if record.chunk_id),
        "content_hashes": sorted({str(record["content_hash"]) for record in records}),
        "records": records,
        "manifest": {
            "schema_version": manifest.get("schema_version"),
            "document_count": manifest.get("document_count"),
            "record_count": manifest.get("record_count"),
            "chunk_count": manifest.get("chunk_count"),
            "records": manifest_records if isinstance(manifest_records, list) else [],
        },
    }
    payload["sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


def _fingerprint_diff(old: Any, current: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(old, dict):
        return {
            "record_count_changed": True,
            "content_hashes_added": current.get("content_hashes", []),
            "content_hashes_removed": [],
        }
    old_hashes = {str(item) for item in old.get("content_hashes", []) if item}
    current_hashes = {str(item) for item in current.get("content_hashes", []) if item}
    return {
        "record_count_changed": old.get("record_count") != current.get("record_count"),
        "document_count_changed": old.get("document_count") != current.get("document_count"),
        "content_hashes_added": sorted(current_hashes - old_hashes),
        "content_hashes_removed": sorted(old_hashes - current_hashes),
    }


def _id_diff(old: list[dict[str, Any]], new: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    old_ids = {str(item.get("id")) for item in old if item.get("id")}
    new_ids = {str(item.get("id")) for item in new if item.get("id")}
    return sorted(new_ids - old_ids), sorted(old_ids - new_ids)


def _query_result(
    kind: str,
    item: dict[str, Any],
    score: int,
    matched_terms: list[str],
    *,
    node_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if kind == "node":
        payload = {
            "kind": "node",
            "id": item.get("id"),
            "type": item.get("type"),
            "label": _node_label(item),
            "score": score,
            "matched_terms": matched_terms,
            "citation_id": item.get("citation_id"),
            "url": item.get("url"),
            "title": item.get("title"),
            "document_id": item.get("document_id"),
            "chunk_id": item.get("id") if item.get("type") == "chunk" else item.get("chunk_id"),
            "content_hash": item.get("content_hash"),
            "excerpt": item.get("excerpt"),
        }
        return {key: value for key, value in payload.items() if value is not None}

    source = node_by_id.get(str(item.get("from") or ""))
    target = node_by_id.get(str(item.get("to") or ""))
    label = f"{_node_label(source)} -> {_node_label(target)}"
    payload = {
        "kind": "edge",
        "id": item.get("id"),
        "type": item.get("type"),
        "label": label,
        "score": score,
        "matched_terms": matched_terms,
        "relationship": item.get("relationship"),
        "citation_id": item.get("citation_id"),
        "url": item.get("url"),
        "title": item.get("title"),
        "document_id": item.get("document_id"),
        "chunk_id": item.get("chunk_id"),
        "content_hash": item.get("content_hash"),
        "excerpt": item.get("excerpt"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _citations_for_results(
    results: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    wanted = {str(result.get("citation_id")) for result in results if result.get("citation_id")}
    citations: list[dict[str, Any]] = []
    for node in nodes:
        if node.get("type") != "source":
            continue
        citation_id = str(node.get("citation_id") or "")
        if citation_id not in wanted:
            continue
        citations.append(
            {
                "citation_id": citation_id,
                "url": node.get("url"),
                "title": node.get("title"),
                "domain": node.get("domain"),
                "path": node.get("path"),
            }
        )
    return citations


def _top_entities(nodes: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    sorted_nodes = sorted(
        nodes,
        key=lambda node: (
            -_safe_int(node.get("source_count")),
            -_safe_int(node.get("count")),
            str(node.get("entity_type") or ""),
            str(node.get("normalized") or ""),
        ),
    )
    return [_public_node(node) for node in sorted_nodes[:limit]]


def _public_node(node: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in node.items()
        if key
        in {
            "id",
            "type",
            "label",
            "entity_type",
            "value",
            "normalized",
            "count",
            "source_count",
            "citation_id",
            "url",
            "title",
        }
        and value is not None
    }


def _entity_in_content(entity: dict[str, Any], content: str) -> bool:
    haystack = content.casefold()
    candidates = {
        str(entity.get("value") or "").strip(),
        str(entity.get("normalized") or "").strip(),
        str(entity.get("label") or "").strip(),
    }
    return any(candidate and candidate.casefold() in haystack for candidate in candidates)


def _entity_excerpt(entity: dict[str, Any], content: str) -> str:
    haystack = content.casefold()
    candidates = [
        str(entity.get("value") or "").strip(),
        str(entity.get("normalized") or "").strip(),
        str(entity.get("label") or "").strip(),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        index = haystack.find(candidate.casefold())
        if index >= 0:
            return _nearest_sentence(content, index, index + len(candidate))
    return _excerpt(content)


def _sentences(content: str) -> list[str]:
    return [
        sentence[:420]
        for sentence in (_clean_text(item) for item in _SENTENCE_SPLIT_RE.split(_clean_text(content)))
        if sentence
    ]


def _relation_label(sentence: str) -> str:
    for label, pattern in _RELATION_PATTERNS:
        if pattern.search(sentence):
            return label
    return "related_in_sentence"


def _nearest_sentence(content: str, start: int, end: int) -> str:
    cleaned = _clean_text(content)
    offset = 0
    for sentence in _SENTENCE_SPLIT_RE.split(cleaned):
        sentence_end = offset + len(sentence)
        if offset <= start <= sentence_end or offset <= end <= sentence_end:
            return sentence[:320]
        offset = sentence_end + 1
    left = max(0, start - 140)
    right = min(len(cleaned), end + 140)
    return cleaned[left:right]


def _score_text(text: str, terms: list[str], phrase: str) -> tuple[int, list[str]]:
    haystack = text.casefold()
    matched = [term for term in terms if term in haystack]
    score = len(matched) * 10
    if phrase and phrase in haystack:
        score += 25
    return score, matched


def _node_search_text(node: dict[str, Any]) -> str:
    fields = [
        node.get("label"),
        node.get("type"),
        node.get("entity_type"),
        node.get("value"),
        node.get("normalized"),
        node.get("url"),
        node.get("title"),
        node.get("citation_id"),
        node.get("chunk_heading"),
        node.get("excerpt"),
    ]
    return " ".join(str(field) for field in fields if field is not None)


def _edge_search_text(edge: dict[str, Any]) -> str:
    fields = [
        edge.get("type"),
        edge.get("relationship"),
        edge.get("url"),
        edge.get("citation_id"),
        edge.get("title"),
        edge.get("document_id"),
        edge.get("chunk_id"),
        edge.get("content_hash"),
        edge.get("excerpt"),
    ]
    return " ".join(str(field) for field in fields if field is not None)


def _node_label(node: dict[str, Any] | None) -> str:
    if not node:
        return "unknown"
    return str(node.get("label") or node.get("title") or node.get("url") or node.get("id") or "unknown")


def _node_by_id(nodes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(node.get("id")): node for node in nodes if node.get("id")}


def _dedupe_by_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "")
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        deduped.append(item)
    return deduped


def _graph_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# Source Graph",
        "",
        f"- Status: {payload['status']}",
        f"- Sources: {summary['source_count']}",
        f"- Documents: {summary['document_count']}",
        f"- Chunks: {summary['chunk_count']}",
        f"- Entities: {summary['entity_count']}",
        f"- Nodes: {summary['node_count']}",
        f"- Edges: {summary['edge_count']}",
        "",
        "## Artifacts",
        "",
    ]
    for label, path in payload["artifacts"].items():
        lines.append(f"- {label}: `{path}`")
    lines.extend(["", "## Top Entities", ""])
    top_entities = payload.get("top_entities")
    if isinstance(top_entities, list) and top_entities:
        for entity in top_entities[:20]:
            lines.append(
                f"- {entity.get('label')} ({entity.get('entity_type')}, {entity.get('count', 0)} mentions)"
            )
    else:
        lines.append("- No entities extracted.")
    lines.append("")
    return "\n".join(lines)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as err:
        raise GraphError(f"Missing graph file: {path}") from err
    except json.JSONDecodeError as err:
        raise GraphError(f"Invalid JSON in {path}: {err}") from err
    if not isinstance(payload, dict):
        raise GraphError(f"Invalid graph JSON in {path}: expected object")
    return payload


def _write_ndjson(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _read_ndjson(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as err:
        raise GraphError(f"Missing graph file: {path}") from err
    records: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as err:
            raise GraphError(f"Invalid NDJSON in {path} line {index}: {err}") from err
        if not isinstance(value, dict):
            raise GraphError(f"Invalid NDJSON in {path} line {index}: expected object")
        records.append(value)
    return records


def _keywords(value: str) -> list[str]:
    return sorted({match.group(0).casefold() for match in _WORD_RE.finditer(value)})


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _excerpt(content: str, *, limit: int = 280) -> str:
    cleaned = _clean_text(content)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "..."


def _artifact_ref(pack_dir: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(pack_dir.resolve()).as_posix()
    except ValueError:
        return str(path)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:24]}"


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _domain(url: str) -> str:
    match = re.match(r"^[a-z][a-z0-9+.-]*://([^/?#]+)", url, re.IGNORECASE)
    if not match:
        return ""
    return match.group(1).split("@")[-1].split(":")[0].lower()
