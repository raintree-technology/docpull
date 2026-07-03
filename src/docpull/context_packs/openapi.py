"""Build local v3 packs from OpenAPI specifications."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml

from ..conversion.chunking import TokenCounter, chunk_markdown
from ..http.client import AsyncHttpClient
from ..http.rate_limiter import PerHostRateLimiter
from ..models.document import DocumentRecord
from ..output_contract import default_rights_state, validate_pack_contract
from ..pipeline.manifest import CorpusManifest
from ..security.robots import RobotsChecker
from ..security.url_validator import UrlValidator
from ..time_utils import utc_now_iso
from .common import ContextPackError, artifact_ref, write_json

OPENAPI_WORKFLOW = "openapi-pack"
DEFAULT_OPENAPI_OUTPUT_DIR = Path("packs/openapi")
MAX_OPENAPI_BYTES = 5_000_000
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "trace"}


def build_openapi_pack(
    source: str | Path,
    *,
    output_dir: Path = DEFAULT_OPENAPI_OUTPUT_DIR,
    chunk_tokens: int = 4000,
) -> dict[str, Any]:
    """Turn an OpenAPI JSON/YAML spec into a v3 raw context pack."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    text, source_url, source_kind = _read_source(source)
    spec = _parse_openapi(text, source=source_url)
    operations = _operation_items(spec, source_url=source_url)
    schemas = _schema_items(spec, source_url=source_url)
    if not operations and not schemas:
        raise ContextPackError("OpenAPI spec contained no operations or component schemas.")

    spec_path = output_dir / "openapi.spec.json"
    index_path = output_dir / "openapi.index.json"
    write_json(spec_path, spec)

    sources_dir = output_dir / "sources"
    sources_dir.mkdir(parents=True, exist_ok=True)
    documents_path = output_dir / "documents.ndjson"
    manifest = CorpusManifest(output_dir, output_format="openapi")
    counter = TokenCounter()
    records: list[DocumentRecord] = []
    source_entries: list[dict[str, Any]] = []
    source_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

    items = [*operations, *schemas]
    with documents_path.open("w", encoding="utf-8") as ndjson:
        for index, item in enumerate(items, start=1):
            markdown = _item_markdown(item, spec=spec)
            source_path = sources_dir / f"{index:03d}-{_slugify(item['title'])}.md"
            source_path.write_text(markdown, encoding="utf-8")
            source_entries.append(
                {
                    "index": index,
                    "url": item["url"],
                    "title": item["title"],
                    "path": artifact_ref(output_dir, source_path),
                    "kind": item["kind"],
                }
            )
            chunks = chunk_markdown(markdown, max_tokens=chunk_tokens, counter=counter)
            if not chunks:
                fallback_markdown = f"# {item['title']}\n\n{markdown}"
                chunks = chunk_markdown(fallback_markdown, max_tokens=chunk_tokens, counter=counter)
            for chunk in chunks:
                record = DocumentRecord.from_page(
                    url=item["url"],
                    title=item["title"],
                    content=chunk.text,
                    metadata={
                        **item["metadata"],
                        "source_url": source_url,
                        "source_kind": source_kind,
                        "source_document_hash": source_hash,
                        "source_path": artifact_ref(output_dir, source_path),
                    },
                    extraction={
                        "workflow": OPENAPI_WORKFLOW,
                        "parsed_at": utc_now_iso(),
                        "openapi_version": _spec_version(spec),
                    },
                    source_type=f"openapi_{item['kind']}",
                    content_type="text/markdown",
                    mime_type="text/markdown",
                    route={
                        "name": "local-openapi-parse",
                        "output_format": "openapi",
                        "source_kind": source_kind,
                        "source_url": source_url,
                        "item_kind": item["kind"],
                    },
                    rights=default_rights_state(),
                    chunk_index=chunk.index,
                    chunk_heading=chunk.heading,
                    token_count=chunk.token_count,
                )
                records.append(record)
                manifest.add_record(record, source_path)
                payload = record.model_dump(mode="json", exclude_none=True)
                ndjson.write(json.dumps(payload, ensure_ascii=False))
                ndjson.write("\n")

    manifest_path = manifest.finalize()
    index_payload = {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "workflow": OPENAPI_WORKFLOW,
        "source": source_url,
        "source_kind": source_kind,
        "title": _spec_title(spec),
        "openapi_version": _spec_version(spec),
        "operation_count": len(operations),
        "schema_count": len(schemas),
        "operations": [_public_item(item) for item in operations],
        "schemas": [_public_item(item) for item in schemas],
    }
    write_json(index_path, index_payload)
    readme_path = output_dir / "OPENAPI.md"
    readme_path.write_text(_summary_markdown(index_payload, source_entries), encoding="utf-8")
    validation = validate_pack_contract(output_dir, level="raw")
    result = {
        "schema_version": 1,
        "generated_at": utc_now_iso(),
        "workflow": OPENAPI_WORKFLOW,
        "status": "completed" if validation["status"] == "pass" else "completed_with_validation_errors",
        "source": source_url,
        "source_kind": source_kind,
        "summary": {
            "operation_count": len(operations),
            "schema_count": len(schemas),
            "record_count": len(records),
            "chunk_count": sum(1 for record in records if record.chunk_id),
        },
        "artifacts": {
            "documents_ndjson": artifact_ref(output_dir, documents_path),
            "corpus_manifest": artifact_ref(output_dir, manifest_path),
            "sources": "sources.md",
            "acquisition_routes": "acquisition.routes.json",
            "openapi_spec": artifact_ref(output_dir, spec_path),
            "openapi_index": artifact_ref(output_dir, index_path),
            "markdown": artifact_ref(output_dir, readme_path),
        },
        "validation": validation,
    }
    write_json(output_dir / "openapi.pack.json", result)
    return result


def _read_source(source: str | Path) -> tuple[str, str, str]:
    value = str(source)
    if value.startswith(("http://", "https://")):
        return _read_remote_source(value), value, "remote"
    path = Path(source).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise ContextPackError(f"OpenAPI source file does not exist: {path}")
    data = path.read_bytes()
    if len(data) > MAX_OPENAPI_BYTES:
        raise ContextPackError(f"OpenAPI source exceeds {MAX_OPENAPI_BYTES} bytes: {path}")
    return data.decode("utf-8", errors="replace"), path.as_uri(), "file"


def _read_remote_source(url: str) -> str:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_read_remote_source_async(url))
    raise ContextPackError("Remote openapi-pack sources cannot be fetched while an event loop is running.")


async def _read_remote_source_async(url: str) -> str:
    validator = UrlValidator(allowed_schemes={"https"})
    validation = validator.validate(url)
    if not validation.is_valid:
        raise ContextPackError(f"Remote OpenAPI source rejected: {validation.rejection_reason}")
    rate_limiter = PerHostRateLimiter(default_delay=0.0, default_concurrent=1)
    async with AsyncHttpClient(
        rate_limiter=rate_limiter,
        url_validator=validator,
        default_timeout=30.0,
    ) as client:
        robots = RobotsChecker(user_agent=client.user_agent, url_validator=validator)
        if not robots.is_allowed(url):
            raise ContextPackError(f"Robots.txt disallows or could not verify remote OpenAPI source: {url}")
        response = await client.get(
            url,
            headers={"Accept": "application/json, application/yaml, text/yaml;q=0.8"},
        )
    if response.status_code >= 400:
        raise ContextPackError(f"Could not fetch OpenAPI source {url}: HTTP {response.status_code}")
    if len(response.content) > MAX_OPENAPI_BYTES:
        raise ContextPackError(f"OpenAPI source exceeds {MAX_OPENAPI_BYTES} bytes: {url}")
    return _decode_response(response.content, response.content_type)


def _decode_response(body: bytes, content_type: str) -> str:
    encoding = "utf-8"
    for part in content_type.split(";"):
        stripped = part.strip()
        if stripped.lower().startswith("charset="):
            encoding = stripped.split("=", 1)[1].strip().strip("\"'") or encoding
            break
    try:
        return body.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace")


def _parse_openapi(text: str, *, source: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as err:
            raise ContextPackError(f"Invalid OpenAPI JSON/YAML {source}: {err}") from err
    if not isinstance(data, dict):
        raise ContextPackError("OpenAPI source must parse to an object.")
    if "openapi" not in data and "swagger" not in data:
        raise ContextPackError("OpenAPI source must include an openapi or swagger version field.")
    if not isinstance(data.get("paths"), dict):
        raise ContextPackError("OpenAPI source must include a paths object.")
    return data


def _operation_items(spec: dict[str, Any], *, source_url: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        return items
    for path, methods in sorted(paths.items()):
        if not isinstance(methods, dict):
            continue
        path_parameters = _list_of_dicts(methods.get("parameters"))
        for method, operation in sorted(methods.items()):
            method_lower = str(method).lower()
            if method_lower not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            method_upper = method_lower.upper()
            title = (
                _first_string(operation.get("summary"), operation.get("operationId"))
                or f"{method_upper} {path}"
            )
            parameters = [*path_parameters, *_list_of_dicts(operation.get("parameters"))]
            item_url = f"{source_url}#operation-{quote(f'{method_lower}-{path}', safe='')}"
            items.append(
                {
                    "kind": "operation",
                    "title": f"{method_upper} {path} - {title}",
                    "url": item_url,
                    "method": method_upper,
                    "path": str(path),
                    "summary": title,
                    "description": _first_string(operation.get("description")) or "",
                    "metadata": {
                        "method": method_upper,
                        "path": str(path),
                        "operation_id": _first_string(operation.get("operationId")),
                        "tags": _string_list(operation.get("tags")),
                        "parameters": parameters,
                        "request_body": operation.get("requestBody"),
                        "responses": operation.get("responses"),
                    },
                }
            )
    return items


def _schema_items(spec: dict[str, Any], *, source_url: str) -> list[dict[str, Any]]:
    components = spec.get("components")
    schemas = components.get("schemas") if isinstance(components, dict) else None
    if not isinstance(schemas, dict):
        return []
    items: list[dict[str, Any]] = []
    for name, schema in sorted(schemas.items()):
        if not isinstance(schema, dict):
            continue
        items.append(
            {
                "kind": "schema",
                "title": f"Schema {name}",
                "url": f"{source_url}#schema-{quote(str(name), safe='')}",
                "schema_name": str(name),
                "metadata": {
                    "schema_name": str(name),
                    "schema": schema,
                    "required": _string_list(schema.get("required")),
                    "properties": schema.get("properties"),
                },
            }
        )
    return items


def _item_markdown(item: dict[str, Any], *, spec: dict[str, Any]) -> str:
    if item["kind"] == "operation":
        return _operation_markdown(item, spec=spec)
    return _schema_markdown(item, spec=spec)


def _operation_markdown(item: dict[str, Any], *, spec: dict[str, Any]) -> str:
    metadata = item["metadata"]
    lines = [
        f"# {item['title']}",
        "",
        f"_source: {item['url']}_",
        "",
        f"- API: {_spec_title(spec)}",
        f"- Method: `{item['method']}`",
        f"- Path: `{item['path']}`",
    ]
    if metadata.get("operation_id"):
        lines.append(f"- Operation ID: `{metadata['operation_id']}`")
    tags = metadata.get("tags")
    if tags:
        lines.append("- Tags: " + ", ".join(f"`{tag}`" for tag in tags))
    lines.append("")
    if item.get("description"):
        lines.extend(["## Description", "", str(item["description"]).strip(), ""])
    parameters = _list_of_dicts(metadata.get("parameters"))
    if parameters:
        lines.extend(
            [
                "## Parameters",
                "",
                "| Name | In | Required | Schema | Description |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for parameter in parameters:
            schema = parameter.get("schema")
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(parameter.get("name")),
                        _md_cell(parameter.get("in")),
                        _md_cell(parameter.get("required")),
                        _md_cell(_schema_label(schema)),
                        _md_cell(parameter.get("description")),
                    ]
                )
                + " |"
            )
        lines.append("")
    if metadata.get("request_body"):
        lines.extend(["## Request Body", "", "```json", _json_block(metadata["request_body"]), "```", ""])
    responses = metadata.get("responses")
    if isinstance(responses, dict) and responses:
        lines.extend(["## Responses", ""])
        for status, response in sorted(responses.items()):
            lines.extend([f"### {status}", "", "```json", _json_block(response), "```", ""])
    lines.extend(["## Raw Operation", "", "```json", _json_block(metadata), "```"])
    return "\n".join(lines).rstrip() + "\n"


def _schema_markdown(item: dict[str, Any], *, spec: dict[str, Any]) -> str:
    metadata = item["metadata"]
    schema = metadata["schema"]
    lines = [
        f"# {item['title']}",
        "",
        f"_source: {item['url']}_",
        "",
        f"- API: {_spec_title(spec)}",
        f"- Schema: `{metadata['schema_name']}`",
        f"- Type: `{schema.get('type', 'object')}`",
        "",
    ]
    description = _first_string(schema.get("description"))
    if description:
        lines.extend(["## Description", "", description, ""])
    properties = schema.get("properties")
    if isinstance(properties, dict) and properties:
        required = set(_string_list(schema.get("required")))
        lines.extend(
            [
                "## Properties",
                "",
                "| Name | Required | Type | Description |",
                "| --- | --- | --- | --- |",
            ]
        )
        for name, property_schema in sorted(properties.items()):
            property_map = property_schema if isinstance(property_schema, dict) else {}
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md_cell(name),
                        _md_cell(name in required),
                        _md_cell(_schema_label(property_map)),
                        _md_cell(property_map.get("description")),
                    ]
                )
                + " |"
            )
        lines.append("")
    lines.extend(["## Raw Schema", "", "```json", _json_block(schema), "```"])
    return "\n".join(lines).rstrip() + "\n"


def _summary_markdown(payload: dict[str, Any], source_entries: list[dict[str, Any]]) -> str:
    lines = [
        "# OpenAPI Pack",
        "",
        f"Source: {payload['source']}",
        f"API: {payload.get('title') or payload['source']}",
        f"OpenAPI version: {payload.get('openapi_version') or 'unknown'}",
        f"Operations: {payload['operation_count']}",
        f"Schemas: {payload['schema_count']}",
        "",
        "## Records",
        "",
    ]
    for entry in source_entries:
        lines.append(f"- [{entry['title']}]({entry['url']}) - `{entry['path']}`")
    return "\n".join(lines).rstrip() + "\n"


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    metadata_map = metadata if isinstance(metadata, dict) else {}
    return {
        "kind": item.get("kind"),
        "title": item.get("title"),
        "url": item.get("url"),
        "method": item.get("method"),
        "path": item.get("path"),
        "operation_id": metadata_map.get("operation_id"),
        "tags": metadata_map.get("tags"),
        "schema_name": metadata_map.get("schema_name"),
    }


def _spec_title(spec: dict[str, Any]) -> str:
    info = spec.get("info")
    if isinstance(info, dict):
        return str(info.get("title") or "OpenAPI").strip() or "OpenAPI"
    return "OpenAPI"


def _spec_version(spec: dict[str, Any]) -> str | None:
    return _first_string(spec.get("openapi"), spec.get("swagger"))


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _schema_label(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    ref = value.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    if isinstance(value.get("type"), str):
        item_type = str(value["type"])
        if item_type == "array" and isinstance(value.get("items"), dict):
            return f"array[{_schema_label(value['items']) or 'object'}]"
        return item_type
    if isinstance(value.get("oneOf"), list):
        return "oneOf"
    if isinstance(value.get("anyOf"), list):
        return "anyOf"
    if isinstance(value.get("allOf"), list):
        return "allOf"
    return "object"


def _md_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("|", "\\|").strip()
    return text[:500]


def _json_block(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True)


_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value).strip("-").lower()
    return slug[:80].strip("-") or "openapi"


__all__ = ["DEFAULT_OPENAPI_OUTPUT_DIR", "build_openapi_pack"]
