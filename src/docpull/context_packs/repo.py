"""Build v3 packs from public GitHub repositories."""

from __future__ import annotations

import asyncio
import io
import json
import re

# repo-pack uses git only as a constrained public GitHub fallback.
import subprocess  # nosec B404
import tarfile
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup, Tag
from defusedxml import ElementTree

from .common import ContextPackError, write_json
from .typed import (
    PrepareLevel,
    TypedPackItem,
    read_https_text,
    simple_summary_markdown,
    typed_http_cache,
    write_typed_pack,
)
from .typed_models import RepoMetadataArtifact

REPO_WORKFLOW = "repo-pack"
DEFAULT_REPO_OUTPUT_DIR = Path("packs/repo")
MAX_REPO_TEXT_BYTES = 200_000
MAX_REPO_ARCHIVE_BYTES = 50_000_000
TEXT_SUFFIXES = {
    ".md",
    ".markdown",
    ".rst",
    ".txt",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
}
IMPORTANT_ROOT_NAMES = {
    "readme",
    "changelog",
    "changes",
    "history",
    "license",
    "licence",
    "copying",
    "security",
    "contributing",
    "code_of_conduct",
    "package",
    "pyproject",
    "setup",
    "cargo",
    "go",
}


def build_repo_pack(
    source: str,
    *,
    output_dir: Path = DEFAULT_REPO_OUTPUT_DIR,
    max_items: int = 30,
    chunk_tokens: int = 4000,
    prepare_level: PrepareLevel = "raw",
    cache_dir: Path | None = None,
    cache_ttl_days: int | None = 7,
) -> dict[str, Any]:
    """Build a v3 pack for a public GitHub repository."""
    with typed_http_cache(cache_dir, ttl_days=cache_ttl_days):
        items, metadata = repo_items_from_github(source, max_items=max_items)
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = output_dir / "repo.metadata.json"
    write_json(metadata_path, RepoMetadataArtifact.model_validate(metadata).model_dump(mode="json"))
    return write_typed_pack(
        workflow=REPO_WORKFLOW,
        output_format="repo",
        output_dir=output_dir,
        items=items,
        pack_filename="repo.pack.json",
        index_filename="repo.index.json",
        items_filename="repo.items.ndjson",
        summary_filename="REPO.md",
        index_payload=metadata,
        summary_markdown=simple_summary_markdown(
            title="Repository Pack",
            source=metadata["html_url"],
            items=items,
        ),
        result_summary={"repo": metadata["full_name"], "sha": metadata["resolved_sha"]},
        objective=f"Review GitHub repository context for {metadata['full_name']}",
        chunk_tokens=chunk_tokens,
        extra_artifacts={"metadata": metadata_path},
        prepare_level=prepare_level,
    )


async def async_build_repo_pack(
    source: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Async-compatible wrapper for SDK callers already inside an event loop."""
    return await asyncio.to_thread(build_repo_pack, source, **kwargs)


def repo_items_from_github(source: str, *, max_items: int = 30) -> tuple[list[TypedPackItem], dict[str, Any]]:
    owner, repo, ref = _parse_github_source(source)
    try:
        return _repo_items_from_github_api(owner, repo, ref, max_items=max_items)
    except (ContextPackError, ValueError) as err:
        return _repo_items_from_github_archive(
            owner,
            repo,
            ref,
            max_items=max_items,
            fallback_reason=str(err),
        )


def _repo_items_from_github_api(
    owner: str,
    repo: str,
    ref: str | None,
    *,
    max_items: int,
) -> tuple[list[TypedPackItem], dict[str, Any]]:
    repo_api = f"https://api.github.com/repos/{owner}/{repo}"
    repo_payload = _read_json(repo_api)
    default_branch = str(repo_payload.get("default_branch") or "main")
    resolved_ref = ref or default_branch
    commit_payload = _read_json(f"{repo_api}/commits/{quote(resolved_ref, safe='')}")
    sha = str(commit_payload.get("sha") or resolved_ref)
    tree_payload = _read_json(f"{repo_api}/git/trees/{quote(sha, safe='')}?recursive=1")
    files = _selected_tree_files(tree_payload, max_items=max(1, max_items - 2))

    items: list[TypedPackItem] = []
    metadata = {
        "schema_version": 3,
        "source": "github",
        "owner": owner,
        "repo": repo,
        "full_name": f"{owner}/{repo}",
        "html_url": str(repo_payload.get("html_url") or f"https://github.com/{owner}/{repo}"),
        "description": repo_payload.get("description"),
        "default_branch": default_branch,
        "requested_ref": ref,
        "resolved_ref": resolved_ref,
        "resolved_sha": sha,
        "license": repo_payload.get("license"),
        "selected_file_count": len(files),
    }
    items.append(_metadata_item(metadata))
    for file_info in files:
        path = str(file_info["path"])
        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{sha}/{quote(path, safe='/')}"
        try:
            text = read_https_text(
                raw_url,
                accept="text/plain, text/markdown, application/json, */*",
                max_bytes=MAX_REPO_TEXT_BYTES,
            )
        except ValueError:
            continue
        items.append(
            _file_item(
                owner,
                repo,
                sha,
                path,
                text.text,
                file_info,
                rights=_repo_rights(metadata.get("license")),
            )
        )
        if len(items) >= max_items:
            break
    if len(items) < max_items:
        releases = _repo_releases(repo_api)
        if releases:
            items.append(_releases_item(owner, repo, sha, releases))
    return items, metadata


def _repo_items_from_github_archive(
    owner: str,
    repo: str,
    ref: str | None,
    *,
    max_items: int,
    fallback_reason: str,
) -> tuple[list[TypedPackItem], dict[str, Any]]:
    default_branch, resolved_ref, sha = _git_resolve_ref(owner, repo, ref)
    releases = _repo_releases_atom(owner, repo)
    html_metadata = _repo_public_html_metadata(owner, repo)
    file_budget = max(1, max_items - 2 if releases else max_items - 1)
    files = _read_github_archive_files(owner, repo, resolved_ref, max_items=file_budget)
    readme = _first_context_file(files, "readme")
    license_file = _first_context_file(files, "license", "licence", "copying")
    license_payload = _license_from_text(license_file)
    description = html_metadata.get("description") or _description_from_readme(readme)
    metadata = {
        "schema_version": 3,
        "source": "github",
        "owner": owner,
        "repo": repo,
        "full_name": f"{owner}/{repo}",
        "html_url": f"https://github.com/{owner}/{repo}",
        "description": description,
        "description_source": "github_html" if html_metadata.get("description") else "readme",
        "default_branch": default_branch,
        "requested_ref": ref,
        "resolved_ref": resolved_ref,
        "resolved_sha": sha,
        "license": license_payload,
        "topics": html_metadata.get("topics", []),
        "html_metadata": html_metadata or None,
        "selected_file_count": len(files),
        "release_count": len(releases),
        "acquisition_method": "git_archive_fallback",
        "fallback_reason": fallback_reason,
    }
    items = [_metadata_item(metadata)]
    for file_info in files:
        items.append(
            _file_item(
                owner,
                repo,
                sha,
                file_info["path"],
                file_info["text"],
                file_info,
                rights=_repo_rights(license_payload),
            )
        )
        if len(items) >= max_items:
            break
    if releases and len(items) < max_items:
        items.append(_releases_item(owner, repo, sha, releases))
    return items, metadata


def _parse_github_source(source: str) -> tuple[str, str, str | None]:
    value = source.strip()
    ref: str | None = None
    if value.startswith(("https://", "http://")):
        parsed = urlparse(value)
        if parsed.netloc.lower() not in {"github.com", "www.github.com"}:
            raise ContextPackError("repo-pack v1 supports public GitHub repository URLs only.")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) < 2:
            raise ContextPackError(f"GitHub repository URL is missing owner/repo: {source}")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
            ref = parts[3]
        return owner, repo, ref
    if "@" in value:
        value, ref = value.rsplit("@", 1)
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)", value)
    if not match:
        raise ContextPackError("repo-pack source must be a GitHub URL or owner/repo[@ref].")
    return match.group(1), match.group(2).removesuffix(".git"), ref


def _read_json(url: str) -> dict[str, Any]:
    response = read_https_text(
        url,
        accept="application/vnd.github+json, application/json",
        headers={"X-GitHub-Api-Version": "2022-11-28"},
    )
    parsed = json.loads(response.text)
    if not isinstance(parsed, dict):
        raise ContextPackError(f"GitHub API response was not an object: {url}")
    return parsed


def _git_resolve_ref(owner: str, repo: str, ref: str | None) -> tuple[str, str, str]:
    try:
        return _git_http_resolve_ref(owner, repo, ref)
    except ContextPackError as http_err:
        try:
            return _git_cli_resolve_ref(owner, repo, ref)
        except ContextPackError as git_err:
            raise ContextPackError(
                f"Could not resolve GitHub ref for {owner}/{repo} without the REST API: {http_err}; {git_err}"
            ) from git_err


def _git_cli_resolve_ref(owner: str, repo: str, ref: str | None) -> tuple[str, str, str]:
    remote = f"https://github.com/{owner}/{repo}.git"
    if ref:
        output = _run_git(["ls-remote", remote, ref, f"refs/heads/{ref}", f"refs/tags/{ref}"])
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 2 and re.fullmatch(r"[0-9a-fA-F]{40}", parts[0]):
                return ref, ref, parts[0]
        raise ContextPackError(f"Could not resolve GitHub ref {owner}/{repo}@{ref}.")

    output = _run_git(["ls-remote", "--symref", remote, "HEAD"])
    default_branch = "main"
    sha = ""
    for line in output.splitlines():
        if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
            default_branch = line.split("refs/heads/", 1)[1].split("\t", 1)[0]
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "HEAD" and re.fullmatch(r"[0-9a-fA-F]{40}", parts[0]):
            sha = parts[0]
    if not sha:
        raise ContextPackError(f"Could not resolve GitHub HEAD for {owner}/{repo}.")
    return default_branch, default_branch, sha


def _git_http_resolve_ref(owner: str, repo: str, ref: str | None) -> tuple[str, str, str]:
    url = f"https://github.com/{owner}/{repo}.git/info/refs?service=git-upload-pack"
    refs = _read_git_upload_pack_refs(url)
    if ref:
        for ref_name in (ref, f"refs/heads/{ref}", f"refs/tags/{ref}"):
            sha = refs["refs"].get(ref_name)
            if sha:
                return ref, ref, sha
        raise ContextPackError(f"Could not resolve GitHub ref {owner}/{repo}@{ref} through Git HTTP.")
    default_branch = refs["default_branch"] or "main"
    sha = refs["head_sha"] or refs["refs"].get(f"refs/heads/{default_branch}") or ""
    if not sha:
        raise ContextPackError(f"Could not resolve GitHub HEAD for {owner}/{repo} through Git HTTP.")
    return default_branch, default_branch, sha


def _read_git_upload_pack_refs(url: str) -> dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        raise ContextPackError(f"Refusing non-GitHub git ref URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "docpull"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec B310
            data = response.read(2_000_001)
    except OSError as err:
        raise ContextPackError(f"Git HTTP ref lookup failed: {err}") from err
    if len(data) > 2_000_000:
        raise ContextPackError("Git HTTP ref advertisement exceeded the repo-pack size limit.")
    refs: dict[str, str] = {}
    default_branch = ""
    head_sha = ""
    for line in _iter_pkt_lines(data):
        if line.startswith("#"):
            continue
        if "\x00" in line:
            line, capabilities = line.split("\x00", 1)
            for capability in capabilities.split():
                if capability.startswith("symref=HEAD:refs/heads/"):
                    default_branch = capability.removeprefix("symref=HEAD:refs/heads/")
        parts = line.split()
        if len(parts) < 2 or not re.fullmatch(r"[0-9a-fA-F]{40}", parts[0]):
            continue
        sha, ref_name = parts[0], parts[1]
        refs[ref_name] = sha
        if ref_name == "HEAD":
            head_sha = sha
    return {"refs": refs, "default_branch": default_branch, "head_sha": head_sha}


def _iter_pkt_lines(data: bytes) -> list[str]:
    lines: list[str] = []
    index = 0
    while index + 4 <= len(data):
        raw_len = data[index : index + 4]
        index += 4
        try:
            packet_len = int(raw_len.decode("ascii"), 16)
        except ValueError:
            break
        if packet_len == 0:
            continue
        if packet_len < 4 or index + packet_len - 4 > len(data):
            break
        payload = data[index : index + packet_len - 4]
        index += packet_len - 4
        lines.append(payload.decode("utf-8", errors="replace").rstrip("\n"))
    return lines


def _run_git(args: list[str]) -> str:
    try:
        completed = subprocess.run(  # nosec B603, B607
            ["git", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        raise ContextPackError(f"Git fallback failed: {err}") from err
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ContextPackError(f"Git fallback failed: {detail or 'git command failed'}")
    return completed.stdout


def _read_github_archive_files(
    owner: str,
    repo: str,
    ref: str,
    *,
    max_items: int,
) -> list[dict[str, Any]]:
    archive_url = f"https://codeload.github.com/{owner}/{repo}/tar.gz/{quote(ref, safe='')}"
    archive = _read_github_archive_bytes(archive_url)
    selected: list[dict[str, Any]] = []
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
        candidates: list[tarfile.TarInfo] = []
        for member in tar.getmembers():
            if not member.isfile() or member.size > MAX_REPO_TEXT_BYTES:
                continue
            path = _archive_member_path(member.name)
            if not path or not _looks_like_context_file(path):
                continue
            candidates.append(member)
        for member in sorted(
            candidates,
            key=lambda item: (_file_rank(_archive_member_path(item.name)), item.name),
        ):
            if len(selected) >= max_items:
                break
            path = _archive_member_path(member.name)
            extracted = tar.extractfile(member)
            if extracted is None:
                continue
            data = extracted.read(MAX_REPO_TEXT_BYTES + 1)
            if len(data) > MAX_REPO_TEXT_BYTES:
                continue
            selected.append(
                {
                    "path": path,
                    "size": member.size,
                    "text": data.decode("utf-8", errors="replace"),
                }
            )
    return selected


def _read_github_archive_bytes(url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != "codeload.github.com":
        raise ContextPackError(f"Refusing non-GitHub archive URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": "docpull"})
    try:
        with urllib.request.urlopen(request, timeout=45) as response:  # nosec B310
            final_url = response.geturl()
            final = urlparse(final_url)
            if final.scheme != "https" or final.netloc.lower() != "codeload.github.com":
                raise ContextPackError(f"GitHub archive redirected to unexpected host: {final_url}")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_REPO_ARCHIVE_BYTES:
                    raise ContextPackError("GitHub archive exceeded the repo-pack fallback size limit.")
                chunks.append(chunk)
            return b"".join(chunks)
    except OSError as err:
        raise ContextPackError(f"Could not fetch GitHub archive fallback: {err}") from err


def _archive_member_path(name: str) -> str:
    parts = [part for part in name.split("/") if part and part not in {".", ".."}]
    if len(parts) <= 1:
        return ""
    return "/".join(parts[1:])


def _repo_releases_atom(owner: str, repo: str) -> list[dict[str, Any]]:
    url = f"https://github.com/{owner}/{repo}/releases.atom"
    try:
        response = read_https_text(url, accept="application/atom+xml, application/xml, text/xml")
    except ValueError:
        return []
    try:
        root = ElementTree.fromstring(response.text)
    except ElementTree.ParseError:
        return []
    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    releases: list[dict[str, Any]] = []
    for entry in root.findall("atom:entry", namespace)[:5]:
        title = _xml_text(entry, "atom:title", namespace)
        link = entry.find("atom:link", namespace)
        releases.append(
            {
                "name": title,
                "tag_name": title,
                "published_at": _xml_text(entry, "atom:published", namespace),
                "html_url": link.get("href") if link is not None else None,
                "body": _xml_text(entry, "atom:content", namespace),
            }
        )
    return releases


def _repo_public_html_metadata(owner: str, repo: str) -> dict[str, Any]:
    """Read non-API repository metadata from the public GitHub HTML page."""
    url = f"https://github.com/{owner}/{repo}"
    try:
        response = read_https_text(
            url,
            accept="text/html, application/xhtml+xml",
            max_bytes=1_500_000,
        )
    except ValueError:
        return {}
    return _parse_repo_html_metadata(response.text, owner=owner, repo=repo)


def _parse_repo_html_metadata(html_text: str, *, owner: str, repo: str) -> dict[str, Any]:
    soup = BeautifulSoup(html_text, "html.parser")
    full_name = f"{owner}/{repo}"
    description = _clean_github_description(
        _meta_content(soup, "og:description")
        or _meta_content(soup, "description")
        or _meta_content(soup, "twitter:description"),
        full_name=full_name,
    )
    topics = _dedupe_text(
        [
            tag.get_text(" ", strip=True)
            for tag in soup.select("a.topic-tag, a[data-ga-click*='topic']")
            if tag.get_text(strip=True)
        ]
    )
    homepage = None
    for link in soup.select("a[rel='nofollow me'], a[rel='nofollow']"):
        href = str(link.get("href") or "").strip()
        if href.startswith(("https://", "http://")) and "github.com" not in urlparse(href).netloc:
            homepage = href
            break
    return {
        key: value
        for key, value in {
            "description": description,
            "topics": topics,
            "homepage": homepage,
        }.items()
        if value
    }


def _meta_content(soup: BeautifulSoup, name: str) -> str | None:
    for attrs in ({"property": name}, {"name": name}):
        tag = soup.find("meta", attrs=attrs)
        if not isinstance(tag, Tag):
            continue
        content = str(tag.get("content") or "").strip()
        if content:
            return content
    return None


def _clean_github_description(value: str | None, *, full_name: str) -> str | None:
    if not value:
        return None
    text = re.sub(r"\s+", " ", value).strip()
    prefixes = (f"GitHub - {full_name}: ", f"{full_name}: ")
    for prefix in prefixes:
        if text.startswith(prefix):
            text = text.removeprefix(prefix).strip()
            break
    if not text or text == full_name:
        return None
    return text[:300]


def _dedupe_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        normalized = re.sub(r"\s+", " ", value).strip()
        if not normalized or normalized in seen:
            continue
        output.append(normalized)
        seen.add(normalized)
    return output


def _xml_text(node: Any, selector: str, namespace: dict[str, str]) -> str | None:
    child = node.find(selector, namespace)
    if child is None or child.text is None:
        return None
    return child.text.strip() or None


def _first_context_file(files: list[dict[str, Any]], *roots: str) -> dict[str, Any] | None:
    wanted = set(roots)
    for file_info in files:
        path = str(file_info.get("path") or "").lower()
        root = Path(path).stem
        if "/" not in path and root in wanted:
            return file_info
    return None


def _description_from_readme(readme: dict[str, Any] | None) -> str | None:
    if not readme:
        return None
    lines = [line.strip() for line in str(readme.get("text") or "").splitlines()]
    for line in lines:
        if not line:
            continue
        if line.startswith("#"):
            continue
        if line.startswith(("[!", "<", "[//]:", "<!--")):
            continue
        return re.sub(r"\s+", " ", line).strip()[:300] or None
    return None


def _license_from_text(license_file: dict[str, Any] | None) -> dict[str, Any] | None:
    if not license_file:
        return None
    text = str(license_file.get("text") or "")
    lowered = text[:4000].lower()
    candidates = (
        ("MIT", "MIT License", "mit license"),
        ("Apache-2.0", "Apache License 2.0", "apache license"),
        ("BSD-3-Clause", "BSD License", "bsd license"),
        ("GPL", "GNU General Public License", "gnu general public license"),
        ("LGPL", "GNU Lesser General Public License", "gnu lesser general public license"),
        ("MPL-2.0", "Mozilla Public License 2.0", "mozilla public license"),
        ("ISC", "ISC License", "isc license"),
    )
    for spdx_id, name, needle in candidates:
        if needle in lowered:
            return {"spdx_id": spdx_id, "name": name, "source": license_file.get("path")}
    return {"spdx_id": "NOASSERTION", "name": "License file present", "source": license_file.get("path")}


def _repo_rights(license_payload: Any) -> dict[str, Any]:
    status = (
        "permissioned" if isinstance(license_payload, dict) and license_payload.get("spdx_id") else "unknown"
    )
    allowed = "allowed_with_conditions" if status == "permissioned" else "unknown"
    return {
        "status": status,
        "license": license_payload,
        "allowed_use": {
            "internal_indexing": "allowed",
            "redistribution": allowed,
            "eval_generation": allowed,
            "model_training": "unknown",
        },
        "obligations": ["preserve license notices"] if status == "permissioned" else [],
        "basis": "repository_license_file" if status == "permissioned" else "license_unknown",
    }


def _selected_tree_files(tree_payload: dict[str, Any], *, max_items: int) -> list[dict[str, Any]]:
    tree = tree_payload.get("tree")
    if not isinstance(tree, list):
        return []
    candidates: list[dict[str, Any]] = []
    for entry in tree:
        if not isinstance(entry, dict) or entry.get("type") != "blob":
            continue
        path = str(entry.get("path") or "")
        size = int(entry.get("size") or 0)
        if size > MAX_REPO_TEXT_BYTES or not _looks_like_context_file(path):
            continue
        candidates.append(entry)
    return sorted(
        candidates, key=lambda item: (_file_rank(str(item.get("path") or "")), str(item.get("path") or ""))
    )[:max_items]


def _looks_like_context_file(path: str) -> bool:
    lowered = path.lower()
    suffix = Path(lowered).suffix
    root = Path(lowered).stem
    first = lowered.split("/", 1)[0]
    if suffix not in TEXT_SUFFIXES and root not in IMPORTANT_ROOT_NAMES:
        return False
    return (
        ("/" not in lowered and root in IMPORTANT_ROOT_NAMES)
        or first in {"docs", "doc", "examples", "example", "samples", "sample"}
        or lowered in {"package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod"}
    )


def _file_rank(path: str) -> int:
    lowered = path.lower()
    if lowered.startswith("readme"):
        return 0
    if lowered.startswith(("changelog", "changes", "history")):
        return 1
    if lowered.startswith(("license", "licence", "copying", "security", "contributing")):
        return 2
    if lowered in {"package.json", "pyproject.toml", "setup.py", "cargo.toml", "go.mod"}:
        return 3
    if lowered.startswith(("docs/", "doc/")):
        return 4
    return 5


def _metadata_item(metadata: dict[str, Any]) -> TypedPackItem:
    markdown = "\n".join(
        [
            f"# Repository: {metadata['full_name']}",
            "",
            f"- URL: {metadata['html_url']}",
            f"- Default branch: `{metadata['default_branch']}`",
            f"- Resolved SHA: `{metadata['resolved_sha']}`",
            f"- Description: {metadata.get('description') or 'none'}",
            f"- License: {_license_name(metadata.get('license'))}",
        ]
    )
    return TypedPackItem(
        title=f"Repository metadata: {metadata['full_name']}",
        url=f"github://{metadata['full_name']}@{metadata['resolved_sha']}/",
        markdown=markdown,
        source_type="github_repository",
        item_kind="metadata",
        metadata=metadata,
        route={
            "source_kind": "github",
            "source_url": metadata["html_url"],
            "resolved_sha": metadata["resolved_sha"],
        },
        rights=_repo_rights(metadata.get("license")),
        public={"sha": metadata["resolved_sha"], "repository": metadata["full_name"]},
    )


def _file_item(
    owner: str,
    repo: str,
    sha: str,
    path: str,
    text: str,
    file_info: dict[str, Any],
    *,
    rights: dict[str, Any] | None = None,
) -> TypedPackItem:
    markdown = "\n".join(
        [
            f"# {path}",
            "",
            f"- Repository: `{owner}/{repo}`",
            f"- SHA: `{sha}`",
            f"- Path: `{path}`",
            "",
            text.strip(),
        ]
    )
    return TypedPackItem(
        title=path,
        url=f"github://{owner}/{repo}@{sha}/{path}",
        markdown=markdown,
        source_type="github_file",
        item_kind="file",
        metadata={"repository": f"{owner}/{repo}", "sha": sha, "path": path, "size": file_info.get("size")},
        route={
            "source_kind": "github",
            "source_url": f"https://github.com/{owner}/{repo}",
            "resolved_sha": sha,
            "path": path,
        },
        rights=rights,
        public={"path": path, "sha": sha, "size": file_info.get("size")},
    )


def _repo_releases(repo_api: str) -> list[dict[str, Any]]:
    try:
        response = read_https_text(
            repo_api + "/releases?per_page=5",
            accept="application/vnd.github+json, application/json",
            headers={"X-GitHub-Api-Version": "2022-11-28"},
        )
    except ValueError:
        return []
    parsed = json.loads(response.text)
    return [item for item in parsed if isinstance(item, dict)] if isinstance(parsed, list) else []


def _releases_item(owner: str, repo: str, sha: str, releases: list[dict[str, Any]]) -> TypedPackItem:
    lines = [f"# Releases: {owner}/{repo}", ""]
    for release in releases:
        lines.append(f"## {release.get('name') or release.get('tag_name') or 'release'}")
        lines.append("")
        lines.append(f"- Tag: `{release.get('tag_name')}`")
        lines.append(f"- Published: {release.get('published_at') or 'unknown'}")
        body = str(release.get("body") or "").strip()
        if body:
            lines.extend(["", body[:5000], ""])
    return TypedPackItem(
        title=f"GitHub releases: {owner}/{repo}",
        url=f"github://{owner}/{repo}@{sha}/releases",
        markdown="\n".join(lines),
        source_type="github_releases",
        item_kind="releases",
        metadata={"repository": f"{owner}/{repo}", "sha": sha, "release_count": len(releases)},
        route={
            "source_kind": "github",
            "source_url": f"https://github.com/{owner}/{repo}/releases",
            "resolved_sha": sha,
        },
        public={"release_count": len(releases)},
    )


def _license_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("spdx_id") or value.get("name") or "unknown")
    return "unknown"


__all__ = ["DEFAULT_REPO_OUTPUT_DIR", "async_build_repo_pack", "build_repo_pack", "repo_items_from_github"]
