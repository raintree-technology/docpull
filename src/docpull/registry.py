"""Shared pack recipes: pinned public sources plus the content hashes a publisher saw.

A recipe never carries fetched content. ``docpull add name@version`` downloads a
recipe from the registry, adds its sources, syncs them locally, and reports which
pages still match the published hashes. Pages change, so drift is reported, not
treated as an error.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, Field, ValidationError, field_validator

from . import __version__
from .discovery_index import _fetch_bytes
from .project import (
    CRAWL_SOURCE_TYPES,
    ProjectError,
    ProjectSource,
    _read_context_lock,
    _read_jsonl,
    _run_dir_for_id,
    find_project_root,
    load_project_config,
    project_paths,
    save_project_config,
    sync_project,
)

SCHEMA_VERSION = 1
REGISTRY_URL_ENV = "DOCPULL_REGISTRY_URL"
DEFAULT_REGISTRY_URL = "https://raw.githubusercontent.com/raintree-technology/docpull-registry/main/"
MAX_INDEX_BYTES = 1_000_000
# About 110 bytes per page, so 10 MB covers roughly 90,000 pages.
MAX_RECIPE_BYTES = 10_000_000
PACK_NAME = r"[a-z0-9][a-z0-9-]{0,63}"
PACK_VERSION = r"[A-Za-z0-9._-]{1,32}"
REF = re.compile(rf"({PACK_NAME})@({PACK_VERSION})")


class RegistryError(ValueError):
    """Invalid recipe, registry entry, or project state."""


class RecipeSource(BaseModel):
    """A public crawl source; typed specs and local paths are refused."""

    name: str
    url: str
    type: str = "auto"
    discovered_urls: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @field_validator("url")
    @classmethod
    def _https(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("recipe source URLs must be absolute https URLs without credentials")
        return value

    @field_validator("type")
    @classmethod
    def _crawl_type(cls, value: str) -> str:
        if value not in CRAWL_SOURCE_TYPES:
            raise ValueError(f"recipe sources must use a crawl type: {', '.join(CRAWL_SOURCE_TYPES)}")
        return value

    def project_source(self) -> ProjectSource:
        # Pinned URLs make the page set reproducible, so they replace live discovery.
        return ProjectSource(
            name=self.name,
            url=self.url,
            type=self.type,  # type: ignore[arg-type]
            discover=not self.discovered_urls,
            discovered_urls=self.discovered_urls,
        )


class Recipe(BaseModel):
    schema_version: int
    name: str = Field(pattern=rf"^{PACK_NAME}$")
    version: str = Field(pattern=rf"^{PACK_VERSION}$")
    description: str = ""
    docpull_version: str = ""
    sources: list[RecipeSource] = Field(min_length=1)
    expected: dict[str, dict[str, str]]

    model_config = {"extra": "forbid"}

    @field_validator("schema_version")
    @classmethod
    def _schema(cls, value: int) -> int:
        if value != SCHEMA_VERSION:
            raise ValueError(f"unsupported recipe schema_version {value}")
        return value

    def canonical(self) -> bytes:
        return (json.dumps(self.model_dump(mode="json"), indent=2, sort_keys=True) + "\n").encode()


def _locked_pages(root: Path) -> dict[str, dict[str, str]]:
    """Map each source to {url: content_hash} for the locked run."""
    paths = project_paths(root)
    lock = _read_context_lock(paths.context_lock)
    if not lock or not isinstance(lock.get("run_id"), str):
        raise RegistryError(f"No locked run in {root}; run `docpull sync` first")
    _, run_dir = _run_dir_for_id(paths, lock["run_id"])
    pages: dict[str, dict[str, str]] = {}
    for record in _read_jsonl(run_dir / "documents.jsonl"):
        metadata = record.get("metadata")
        source = metadata.get("docpull_project_source") if isinstance(metadata, dict) else None
        url, digest = record.get("url"), record.get("content_hash")
        if isinstance(source, str) and isinstance(url, str) and isinstance(digest, str):
            pages.setdefault(source, {})[url] = digest
    return pages


def export_recipe(root: Path, name: str, version: str, description: str = "") -> Recipe:
    """Build a recipe from a synced project's sources and locked page hashes."""
    try:
        root = find_project_root(root)
        config = load_project_config(root)
        pages = _locked_pages(root)
    except ProjectError as err:
        raise RegistryError(str(err)) from err
    private = [source.name for source in config.sources if source.auth]
    if private:
        raise RegistryError(f"Sources with auth cannot be shared: {', '.join(private)}")
    try:
        return Recipe(
            schema_version=SCHEMA_VERSION,
            name=name,
            version=version,
            description=description,
            docpull_version=__version__,
            sources=[
                RecipeSource(name=s.name, url=s.url, type=s.type, discovered_urls=s.discovered_urls)
                for s in config.sources
            ],
            expected={
                source.name: dict(sorted(pages.get(source.name, {}).items())) for source in config.sources
            },
        )
    except ValidationError as err:
        raise RegistryError(f"Project cannot be exported as a recipe: {err}") from err


def registry_url() -> str:
    url = os.environ.get(REGISTRY_URL_ENV) or DEFAULT_REGISTRY_URL
    return url if url.endswith("/") else url + "/"


async def fetch_recipe(ref: str, base_url: str | None = None) -> Recipe:
    """Resolve ``name@version`` through the registry index and verify the recipe's sha256."""
    match = REF.fullmatch(ref)
    if not match:
        raise RegistryError(f"Pack references look like name@version: {ref!r}")
    name, version = match.groups()
    base = base_url or registry_url()
    try:
        index = json.loads(await _fetch_bytes(urljoin(base, "index.json"), MAX_INDEX_BYTES))
    except json.JSONDecodeError as err:
        raise RegistryError("Registry index is not valid JSON") from err
    if not isinstance(index, dict) or index.get("schema_version") != SCHEMA_VERSION:
        raise RegistryError(f"Unsupported registry index; expected schema_version {SCHEMA_VERSION}")
    pack = (index.get("packs") or {}).get(name)
    if not isinstance(pack, dict):
        raise RegistryError(f"Registry has no pack named {name}")
    if version == "latest":
        version = str(pack.get("latest") or "")
    entry = (pack.get("versions") or {}).get(version)
    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
        raise RegistryError(f"Registry has no version {version or 'latest'} of {name}")
    if not re.fullmatch(r"[0-9a-f]{64}", str(entry.get("sha256"))):
        raise RegistryError(f"Registry entry for {name}@{version} has no valid sha256")
    raw = await _fetch_bytes(urljoin(base, entry["path"]), MAX_RECIPE_BYTES)
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise RegistryError(f"sha256 mismatch for {name}@{version}; refusing the recipe")
    try:
        recipe = Recipe.model_validate_json(raw)
    except ValidationError as err:
        raise RegistryError(f"Invalid recipe {name}@{version}: {err}") from err
    if (recipe.name, recipe.version) != (name, version):
        raise RegistryError(f"Registry served {recipe.name}@{recipe.version} for {name}@{version}")
    return recipe


def _recipe_path(root: Path, name: str, version: str) -> Path:
    return project_paths(root).state / "registry" / f"{name}@{version}.json"


def install_recipe(recipe: Recipe, root: Path | None = None) -> dict[str, Any]:
    """Add a recipe's sources, sync them, and return the verification report."""
    try:
        project_root = find_project_root(root or Path.cwd())
        config = load_project_config(project_root)
        sources = [source.project_source() for source in recipe.sources]
    except (ProjectError, ValidationError) as err:
        raise RegistryError(str(err)) from err
    names = {source.name for source in config.sources}
    urls = {source.url for source in config.sources}
    clashes = [s.name for s in sources if s.name in names or s.url in urls]
    if clashes:
        raise RegistryError(f"Project already has sources named or located like: {', '.join(clashes)}")
    save_project_config(project_root, config.model_copy(update={"sources": [*config.sources, *sources]}))
    target = _recipe_path(project_root, recipe.name, recipe.version)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(recipe.canonical())
    try:
        sync_project(root=project_root)
    except ProjectError as err:
        raise RegistryError(f"Sources added, but sync failed: {err}") from err
    return verify_recipe(project_root, f"{recipe.name}@{recipe.version}")


def verify_recipe(root: Path, ref: str) -> dict[str, Any]:
    """Compare the locked run's pages for each recipe source with the published hashes."""
    match = REF.fullmatch(ref)
    if not match:
        raise RegistryError(f"Pack references look like name@version: {ref!r}")
    try:
        project_root = find_project_root(root)
        path = _recipe_path(project_root, *match.groups())
        recipe = Recipe.model_validate_json(path.read_bytes())
        pages = _locked_pages(project_root)
    except FileNotFoundError as err:
        raise RegistryError(f"{ref} is not installed in this project; run `docpull add {ref}`") from err
    except (ProjectError, ValidationError) as err:
        raise RegistryError(str(err)) from err
    report: dict[str, Any] = {"pack": ref, "sources": {}}
    totals = {"expected": 0, "matched": 0, "changed": 0, "missing": 0, "new": 0}
    for name, expected in recipe.expected.items():
        got = pages.get(name, {})
        counts = {
            "expected": len(expected),
            "matched": sum(1 for url, digest in expected.items() if got.get(url) == digest),
            "changed": sum(1 for url, digest in expected.items() if url in got and got[url] != digest),
            "missing": sum(1 for url in expected if url not in got),
            "new": sum(1 for url in got if url not in expected),
        }
        report["sources"][name] = counts
        for key, value in counts.items():
            totals[key] += value
    report.update(totals)
    report["match_ratio"] = round(totals["matched"] / totals["expected"], 4) if totals["expected"] else 1.0
    return report


def _print_report(report: dict[str, Any]) -> None:
    print(
        f"{report['pack']}: {report['matched']}/{report['expected']} pages match the published hashes "
        f"({report['changed']} changed, {report['missing']} missing, {report['new']} new)"
    )


def run_add_recipe_cli(ref: str) -> int:
    try:
        report = install_recipe(asyncio.run(fetch_recipe(ref)))
    except (RegistryError, ValueError) as err:
        print(f"docpull add: {err}", file=sys.stderr)
        return 1
    _print_report(report)
    return 0


def run_registry_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull registry", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export", help="Write a recipe from this synced project")
    export.add_argument("--name", required=True)
    export.add_argument("--version", required=True)
    export.add_argument("--description", default="")
    export.add_argument("--out", type=Path, help="Recipe file (default: <name>@<version>.json)")
    verify = subparsers.add_parser("verify", help="Compare the latest sync with an installed recipe")
    verify.add_argument("ref", help="name@version")
    verify.add_argument("--json", action="store_true", dest="json_output")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            recipe = export_recipe(Path.cwd(), args.name, args.version, args.description)
            out = args.out or Path(f"{args.name}@{args.version}.json")
            data = recipe.canonical()
            out.write_bytes(data)
            pages = sum(len(pages) for pages in recipe.expected.values())
            print(f"Wrote {out}: {len(recipe.sources)} source(s), {pages} pages")
            print(f"sha256 {hashlib.sha256(data).hexdigest()}")
            unpinned = [s.name for s in recipe.sources if not s.discovered_urls]
            if unpinned:
                print(
                    f"warning: {', '.join(unpinned)} has no pinned URLs; run `docpull add URL --discover` "
                    "or sync with discovery so consumers fetch the same pages",
                    file=sys.stderr,
                )
        else:
            report = verify_recipe(Path.cwd(), args.ref)
            if args.json_output:
                print(json.dumps(report, indent=2, sort_keys=True))
            else:
                _print_report(report)
    except RegistryError as err:
        print(f"docpull registry: {err}", file=sys.stderr)
        return 1
    return 0


__all__ = [
    "Recipe",
    "RegistryError",
    "export_recipe",
    "fetch_recipe",
    "install_recipe",
    "run_registry_cli",
    "verify_recipe",
]
