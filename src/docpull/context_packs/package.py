"""Build v3 packs from package registry metadata."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from .common import ContextPackError, write_json
from .repo import repo_items_from_github
from .typed import (
    PrepareLevel,
    TypedPackItem,
    read_https_text,
    simple_summary_markdown,
    typed_http_cache,
    write_typed_pack,
)
from .typed_models import PackageMetadataArtifact

PACKAGE_WORKFLOW = "package-pack"
DEFAULT_PACKAGE_OUTPUT_DIR = Path("packs/package")


def build_package_pack(
    source: str,
    *,
    output_dir: Path = DEFAULT_PACKAGE_OUTPUT_DIR,
    max_items: int = 25,
    chunk_tokens: int = 4000,
    include_repo: bool = False,
    prepare_level: PrepareLevel = "raw",
    cache_dir: Path | None = None,
    cache_ttl_days: int | None = 7,
) -> dict[str, Any]:
    """Build a package registry context pack from npm or PyPI metadata."""
    with typed_http_cache(cache_dir, ttl_days=cache_ttl_days):
        ecosystem, name = _parse_package_source(source)
        if ecosystem == "npm":
            items, metadata = _npm_items(name, max_items=max_items)
        else:
            items, metadata = _pypi_items(name, max_items=max_items)

        if include_repo and len(items) < max_items:
            repo_url = _github_repo_source(metadata.get("repository_url"))
            if repo_url:
                try:
                    repo_items, repo_metadata = repo_items_from_github(
                        repo_url,
                        max_items=max_items - len(items),
                    )
                    metadata["included_repo"] = repo_metadata
                    items.extend(repo_items)
                except ContextPackError as err:
                    metadata["include_repo_error"] = str(err)
                except ValueError as err:
                    metadata["include_repo_error"] = str(err)

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dir / "package.metadata.json"
    write_json(metadata_path, PackageMetadataArtifact.model_validate(metadata).model_dump(mode="json"))
    return write_typed_pack(
        workflow=PACKAGE_WORKFLOW,
        output_format="package",
        output_dir=output_dir,
        items=items[:max_items],
        pack_filename="package.pack.json",
        index_filename="package.index.json",
        items_filename="package.items.ndjson",
        summary_filename="PACKAGE.md",
        index_payload=metadata,
        summary_markdown=simple_summary_markdown(
            title="Package Pack",
            source=f"{ecosystem}:{name}",
            items=items[:max_items],
        ),
        result_summary={
            "ecosystem": ecosystem,
            "package": name,
            "latest_version": metadata.get("latest_version"),
        },
        objective=f"Review {ecosystem} package context for {name}",
        chunk_tokens=chunk_tokens,
        extra_artifacts={"metadata": metadata_path},
        prepare_level=prepare_level,
    )


async def async_build_package_pack(
    source: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Async-compatible wrapper for SDK callers already inside an event loop."""
    return await asyncio.to_thread(build_package_pack, source, **kwargs)


def _parse_package_source(source: str) -> tuple[str, str]:
    if ":" not in source:
        raise ContextPackError("package-pack source must be npm:<name> or pypi:<name>.")
    ecosystem, name = source.split(":", 1)
    ecosystem = ecosystem.strip().lower()
    name = name.strip()
    if ecosystem not in {"npm", "pypi"} or not name:
        raise ContextPackError("package-pack source must be npm:<name> or pypi:<name>.")
    return ecosystem, name


def _npm_items(name: str, *, max_items: int) -> tuple[list[TypedPackItem], dict[str, Any]]:
    url = f"https://registry.npmjs.org/{quote(name, safe='@')}"
    payload = _read_json(url, accept="application/json")
    dist_tags = _dict_value(payload.get("dist-tags"))
    latest = str(dist_tags.get("latest") or "")
    versions = _dict_value(payload.get("versions"))
    latest_payload = _dict_value(versions.get(latest))
    metadata = {
        "schema_version": 3,
        "ecosystem": "npm",
        "name": payload.get("name") or name,
        "description": payload.get("description") or latest_payload.get("description"),
        "latest_version": latest,
        "license": latest_payload.get("license") or payload.get("license"),
        "repository_url": _repo_url(latest_payload.get("repository") or payload.get("repository")),
        "homepage": latest_payload.get("homepage") or payload.get("homepage"),
        "version_count": len(versions),
        "registry_url": url,
    }
    items = [_package_metadata_item(metadata)]
    readme = str(latest_payload.get("readme") or payload.get("readme") or "").strip()
    if readme and len(items) < max_items:
        items.append(_package_text_item(metadata, "readme", "README", readme))
    if latest_payload and len(items) < max_items:
        items.append(_npm_version_item(metadata, latest_payload))
    if versions and len(items) < max_items:
        items.append(_release_history_item(metadata, sorted(versions)[-20:]))
    return items, metadata


def _pypi_items(name: str, *, max_items: int) -> tuple[list[TypedPackItem], dict[str, Any]]:
    url = f"https://pypi.org/pypi/{quote(name, safe='')}/json"
    payload = _read_json(url, accept="application/json")
    info = _dict_value(payload.get("info"))
    releases = _dict_value(payload.get("releases"))
    latest = str(info.get("version") or "")
    metadata = {
        "schema_version": 3,
        "ecosystem": "pypi",
        "name": info.get("name") or name,
        "description": info.get("summary") or info.get("description"),
        "latest_version": latest,
        "license": info.get("license") or info.get("license_expression"),
        "repository_url": _pypi_project_url(info, "Source") or _pypi_project_url(info, "Repository"),
        "homepage": info.get("home_page") or _pypi_project_url(info, "Homepage"),
        "version_count": len(releases),
        "registry_url": url,
        "requires_python": info.get("requires_python"),
    }
    items = [_package_metadata_item(metadata)]
    description = str(info.get("description") or "").strip()
    if description and len(items) < max_items:
        items.append(_package_text_item(metadata, "description", "Description", description))
    if latest and latest in releases and len(items) < max_items:
        items.append(_pypi_version_item(metadata, latest, releases.get(latest) or []))
    if releases and len(items) < max_items:
        items.append(_release_history_item(metadata, sorted(releases)[-20:]))
    return items, metadata


def _read_json(url: str, *, accept: str) -> dict[str, Any]:
    response = read_https_text(url, accept=accept)
    parsed = json.loads(response.text)
    if not isinstance(parsed, dict):
        raise ContextPackError(f"Registry response was not an object: {url}")
    return parsed


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _package_metadata_item(metadata: dict[str, Any]) -> TypedPackItem:
    title = f"{metadata['ecosystem']} package: {metadata['name']}"
    markdown = "\n".join(
        [
            "# " + title,
            "",
            f"- Latest version: `{metadata.get('latest_version') or 'unknown'}`",
            f"- License: {metadata.get('license') or 'unknown'}",
            f"- Registry: {metadata.get('registry_url')}",
            f"- Repository: {metadata.get('repository_url') or 'unknown'}",
            f"- Homepage: {metadata.get('homepage') or 'unknown'}",
            "",
            str(metadata.get("description") or "").strip(),
        ]
    )
    return TypedPackItem(
        title=title,
        url=str(metadata["registry_url"]),
        markdown=markdown,
        source_type="package_metadata",
        item_kind="metadata",
        metadata=metadata,
        route={"source_kind": metadata["ecosystem"], "source_url": metadata["registry_url"]},
        rights=_package_rights(metadata),
        public={
            "ecosystem": metadata["ecosystem"],
            "package": metadata["name"],
            "latest_version": metadata.get("latest_version"),
        },
    )


def _package_text_item(metadata: dict[str, Any], kind: str, label: str, text: str) -> TypedPackItem:
    title = f"{metadata['name']} {label}"
    return TypedPackItem(
        title=title,
        url=f"{metadata['registry_url']}#{kind}",
        markdown=f"# {title}\n\n{text.strip()}",
        source_type="package_document",
        item_kind=kind,
        metadata={
            "ecosystem": metadata["ecosystem"],
            "package": metadata["name"],
            "latest_version": metadata.get("latest_version"),
        },
        route={"source_kind": metadata["ecosystem"], "source_url": metadata["registry_url"]},
        rights=_package_rights(metadata),
        public={"ecosystem": metadata["ecosystem"], "package": metadata["name"]},
    )


def _npm_version_item(metadata: dict[str, Any], version: dict[str, Any]) -> TypedPackItem:
    deps = _dict_value(version.get("dependencies"))
    markdown = "\n".join(
        [
            f"# {metadata['name']} {metadata.get('latest_version')}",
            "",
            f"- Install: `npm install {metadata['name']}@{metadata.get('latest_version')}`",
            f"- Dependencies: {len(deps)}",
            "",
            "```json",
            json.dumps(
                {"dependencies": deps, "engines": version.get("engines")}, indent=2, ensure_ascii=False
            ),
            "```",
        ]
    )
    return TypedPackItem(
        title=f"{metadata['name']} npm version {metadata.get('latest_version')}",
        url=f"{metadata['registry_url']}#version-{metadata.get('latest_version')}",
        markdown=markdown,
        source_type="package_version",
        item_kind="version",
        metadata={
            "ecosystem": "npm",
            "package": metadata["name"],
            "version": metadata.get("latest_version"),
            "dependencies": deps,
        },
        route={"source_kind": "npm", "source_url": metadata["registry_url"]},
        rights=_package_rights(metadata),
        public={"version": metadata.get("latest_version"), "dependency_count": len(deps)},
    )


def _pypi_version_item(metadata: dict[str, Any], version: str, files: list[Any]) -> TypedPackItem:
    file_rows = [file for file in files if isinstance(file, dict)]
    markdown = "\n".join(
        [
            f"# {metadata['name']} {version}",
            "",
            f"- Install: `pip install {metadata['name']}=={version}`",
            f"- Files: {len(file_rows)}",
            f"- Requires Python: {metadata.get('requires_python') or 'unknown'}",
        ]
    )
    return TypedPackItem(
        title=f"{metadata['name']} PyPI version {version}",
        url=f"{metadata['registry_url']}#version-{version}",
        markdown=markdown,
        source_type="package_version",
        item_kind="version",
        metadata={
            "ecosystem": "pypi",
            "package": metadata["name"],
            "version": version,
            "file_count": len(file_rows),
        },
        route={"source_kind": "pypi", "source_url": metadata["registry_url"]},
        rights=_package_rights(metadata),
        public={"version": version, "file_count": len(file_rows)},
    )


def _release_history_item(metadata: dict[str, Any], versions: list[str]) -> TypedPackItem:
    markdown = "# Release history\n\n" + "\n".join(f"- `{version}`" for version in versions)
    return TypedPackItem(
        title=f"{metadata['name']} release history",
        url=f"{metadata['registry_url']}#release-history",
        markdown=markdown,
        source_type="package_releases",
        item_kind="releases",
        metadata={"ecosystem": metadata["ecosystem"], "package": metadata["name"], "versions": versions},
        route={"source_kind": metadata["ecosystem"], "source_url": metadata["registry_url"]},
        rights=_package_rights(metadata),
        public={"version_count": len(versions)},
    )


def _package_rights(metadata: dict[str, Any]) -> dict[str, Any]:
    license_value = str(metadata.get("license") or "").strip()
    lowered = license_value.lower()
    if not license_value:
        status = "unknown"
        redistribution = "unknown"
        eval_generation = "unknown"
    elif lowered in {"unlicensed", "proprietary", "private", "unknown"}:
        status = "restricted"
        redistribution = "denied"
        eval_generation = "denied"
    else:
        status = "permissioned"
        redistribution = "allowed_with_conditions"
        eval_generation = "allowed_with_conditions"
    return {
        "status": status,
        "license": license_value or None,
        "allowed_use": {
            "internal_indexing": "allowed",
            "redistribution": redistribution,
            "eval_generation": eval_generation,
            "model_training": "unknown",
        },
        "obligations": ["comply with package license terms"] if status == "permissioned" else [],
        "basis": "registry_license_metadata" if license_value else "license_unknown",
    }


def _repo_url(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        raw = value.get("url") or value.get("web")
        return str(raw) if raw else None
    return None


def _pypi_project_url(info: dict[str, Any], key: str) -> str | None:
    urls = info.get("project_urls")
    if isinstance(urls, dict):
        value = urls.get(key)
        return str(value) if value else None
    return None


def _github_repo_source(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    text = re.sub(r"^git\+", "", text)
    text = re.sub(r"\.git$", "", text)
    parsed = urlparse(text)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"git", "https"} or hostname != "github.com":
        return None
    if parsed.username or parsed.password:
        return None
    path = parsed.path.strip("/")
    if "/" not in path:
        return None
    return f"https://github.com/{path}"


__all__ = ["DEFAULT_PACKAGE_OUTPUT_DIR", "async_build_package_pack", "build_package_pack"]
