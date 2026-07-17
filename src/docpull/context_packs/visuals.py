"""Local image and screenshot context packs."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil

# screenshot-pack launches an explicit local renderer command behind a trust env gate.
import subprocess  # nosec B404
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from ..policy import PolicyConfig
from .common import (
    CONTEXT_PACK_SCHEMA_VERSION,
    AssetRef,
    ContextPackError,
    ContextPackRun,
    PageSnapshot,
    append_ndjson,
    artifact_ref,
    css_url_values,
    domain_from_input,
    ensure_policy_for_domain,
    fetch_asset_blocking,
    fetch_pages_blocking,
    homepage_url_for_domain,
    public_url,
    quote_markdown,
    soup_for,
    status_from_errors,
    write_basic_pack_files,
    write_json,
)

IMAGE_WORKFLOW = "image-pack"
SCREENSHOT_WORKFLOW = "screenshot-pack"
DEFAULT_IMAGE_OUTPUT_DIR = Path("packs/images")
DEFAULT_SCREENSHOT_OUTPUT_DIR = Path("packs/screenshot")
IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml", "image/x-icon"}
MARKDOWN_IMAGE_RE = re.compile(r"!\[[^\]]*]\(([^)]+)\)")
VIEWPORT_RE = re.compile(r"^(?P<width>[1-9]\d{1,4})x(?P<height>[1-9]\d{1,4})$")
ALLOWED_SCREENSHOT_WAITS = {"load", "domcontentloaded", "networkidle"}


def build_image_pack(
    url_or_pack: str | Path,
    *,
    output_dir: Path = DEFAULT_IMAGE_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    download_assets: bool = True,
    max_assets: int = 40,
) -> dict[str, Any]:
    """Extract a local image manifest from a URL or existing pack."""
    output_dir = output_dir.resolve()
    local_pack_mode = Path(url_or_pack).expanduser().exists()
    pages, run = _image_pages_from_input(url_or_pack, policy=policy, output_dir=output_dir)
    if not pages:
        raise ContextPackError("No image-pack evidence pages found.")
    domain = domain_from_input(pages[0].url) or (urlparse(pages[0].url).hostname or "")
    run.policy = ensure_policy_for_domain(policy, domain) if domain else (policy or PolicyConfig())
    candidates = _image_candidates(pages)
    assets: list[AssetRef] = []
    for candidate in candidates[:max_assets]:
        asset = AssetRef(candidate["url"], candidate["kind"], candidate["source_url"])
        if download_assets and domain and not local_pack_mode:
            asset = fetch_asset_blocking(
                candidate["url"],
                output_dir=output_dir / "assets" / "images",
                source_url=candidate["source_url"],
                kind=candidate["kind"],
                allowed_domains=[domain],
                allowed_content_types=IMAGE_CONTENT_TYPES,
                max_bytes=1_500_000,
                run=run,
            )
        assets.append(asset)
    images_path = output_dir / "images.ndjson"
    assets_path = output_dir / "image.assets.json"
    image_records = [asset.to_dict() for asset in assets]
    append_ndjson(images_path, image_records)
    write_json(
        assets_path,
        {
            "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
            "generated_at": _generated_at(),
            "asset_count": len(image_records),
            "assets": image_records,
        },
    )
    result_payload = {
        "workflow": IMAGE_WORKFLOW,
        "provider": "local",
        "status": status_from_errors(run.errors),
        "input": {
            "value": str(url_or_pack),
            "download_assets": download_assets and not local_pack_mode,
        },
        "summary": {
            "candidate_count": len(candidates),
            "asset_count": len(image_records),
            "downloaded_count": sum(1 for item in image_records if item.get("status") == "downloaded"),
        },
        "images": image_records,
        "warnings": run.warnings,
        "errors": run.errors,
        "replay_config": {
            "url_or_pack": str(url_or_pack),
            "download_assets": download_assets,
            "max_assets": max_assets,
        },
    }
    return write_basic_pack_files(
        run=run,
        pages=pages,
        result_filename="image.result.json",
        result_payload=result_payload,
        markdown_filename="VISUALS.md",
        markdown_text=_images_markdown(image_records, pages),
        pack_filename="image.pack.json",
        extra_artifacts={
            "images_ndjson": artifact_ref(output_dir, images_path),
            "image_assets": artifact_ref(output_dir, assets_path),
        },
    )


def capture_screenshot_pack(
    url: str,
    *,
    output_dir: Path = DEFAULT_SCREENSHOT_OUTPUT_DIR,
    policy: PolicyConfig | None = None,
    viewport: str = "1280x720",
    full_page: bool = False,
    wait_for: str = "load",
    agent_browser_binary: str | None = None,
) -> dict[str, Any]:
    """Capture a PNG screenshot through an explicit local renderer command."""
    _validate_screenshot_options(viewport=viewport, wait_for=wait_for)
    if os.environ.get("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS") != "1":
        raise ContextPackError(
            "screenshot-pack requires DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 because it launches a browser."
        )
    domain = domain_from_input(url)
    if not domain:
        raise ContextPackError("screenshot-pack requires a URL with a resolvable domain.")
    effective_policy = ensure_policy_for_domain(policy, domain)
    allowed, reason = effective_policy.allows_url(public_url(url))
    if not allowed:
        raise ContextPackError(f"URL denied by source policy: {reason}")
    output_dir = output_dir.resolve()
    run = ContextPackRun(
        workflow=SCREENSHOT_WORKFLOW,
        output_dir=output_dir,
        policy=effective_policy,
        input_value=url,
    )
    page = _placeholder_page(url)
    screenshot_dir = output_dir / "screenshots"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = screenshot_dir / "page.png"
    binary = agent_browser_binary or os.environ.get("DOCPULL_AGENT_BROWSER_BIN", "agent-browser")
    payload = _run_screenshot_command(
        binary=binary,
        url=public_url(url),
        output_path=screenshot_path,
        viewport=viewport,
        full_page=full_page,
        wait_for=wait_for,
    )
    result_payload = {
        "workflow": SCREENSHOT_WORKFLOW,
        "provider": "local",
        "status": "completed",
        "input": {"url": public_url(url), "viewport": viewport, "full_page": full_page},
        "summary": {
            "screenshot_count": 1,
            "bytes": payload["bytes"],
            "sha256": payload["sha256"],
        },
        "screenshots": [payload],
        "warnings": run.warnings,
        "errors": run.errors,
        "replay_config": {
            "url": url,
            "viewport": viewport,
            "full_page": full_page,
            "wait_for": wait_for,
        },
    }
    return write_basic_pack_files(
        run=run,
        pages=[page],
        result_filename="screenshot.result.json",
        result_payload=result_payload,
        markdown_filename="SCREENSHOT.md",
        markdown_text=_screenshot_markdown(payload),
        pack_filename="screenshot.pack.json",
        extra_artifacts={"screenshot": payload["path"]},
    )


def _validate_screenshot_options(*, viewport: str, wait_for: str) -> None:
    match = VIEWPORT_RE.fullmatch(viewport)
    if not match:
        raise ContextPackError("viewport must use WIDTHxHEIGHT with positive integer dimensions.")
    width = int(match.group("width"))
    height = int(match.group("height"))
    if width > 10000 or height > 10000:
        raise ContextPackError("viewport dimensions must be 10000 pixels or less.")
    if wait_for not in ALLOWED_SCREENSHOT_WAITS:
        allowed = ", ".join(sorted(ALLOWED_SCREENSHOT_WAITS))
        raise ContextPackError(f"wait_for must be one of: {allowed}.")


def _image_pages_from_input(
    url_or_pack: str | Path,
    *,
    policy: PolicyConfig | None,
    output_dir: Path,
) -> tuple[list[PageSnapshot], ContextPackRun]:
    path = Path(url_or_pack)
    run = ContextPackRun(
        workflow=IMAGE_WORKFLOW,
        output_dir=output_dir,
        policy=policy or PolicyConfig(),
        input_value=str(url_or_pack),
    )
    if path.exists() and path.is_dir():
        return _pages_from_pack(path), run
    value = str(url_or_pack)
    domain = domain_from_input(value)
    if not domain:
        raise ContextPackError("image-pack URL input must resolve to a domain.")
    run.policy = ensure_policy_for_domain(policy, domain)
    start_url = public_url(value if "://" in value else homepage_url_for_domain(domain))
    return fetch_pages_blocking([start_url], run=run, max_pages=1), run


def _pages_from_pack(pack_dir: Path) -> list[PageSnapshot]:
    records_path = pack_dir / "documents.ndjson"
    if not records_path.exists():
        raise ContextPackError(f"Pack has no documents.ndjson: {pack_dir}")
    pages: list[PageSnapshot] = []
    for line in records_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        url = str(record.get("url") or "")
        content = str(record.get("content") or "")
        if url and content:
            metadata_raw = record.get("metadata")
            extraction_raw = record.get("extraction")
            metadata: dict[str, Any] = metadata_raw if isinstance(metadata_raw, dict) else {}
            extraction: dict[str, Any] = extraction_raw if isinstance(extraction_raw, dict) else {}
            pages.append(
                PageSnapshot(
                    url=public_url(url),
                    title=str(record.get("title") or "") or None,
                    html="",
                    markdown=content,
                    metadata=metadata,
                    extraction=extraction,
                    source_type=str(record.get("source_type") or "pack_record"),
                )
            )
    return pages


def _image_candidates(pages: list[PageSnapshot]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for page in pages:
        soup = soup_for(page)
        for tag in soup.find_all("img"):
            for url in _urls_from_img(tag, page.url):
                _add_image(output, seen, url, "img", page.url)
        for tag in soup.find_all("source"):
            srcset = str(tag.get("srcset") or "")
            for url in _urls_from_srcset(srcset, page.url):
                _add_image(output, seen, url, "source", page.url)
        for tag in soup.find_all("meta"):
            name = str(tag.get("property") or tag.get("name") or "").lower()
            content = str(tag.get("content") or "").strip()
            if content and name in {"og:image", "twitter:image"}:
                _add_image(output, seen, public_url(urljoin(page.url, content)), "metadata", page.url)
        for tag in soup.find_all("link"):
            rel = " ".join(str(item).lower() for item in tag.get("rel", []))
            href = str(tag.get("href") or "").strip()
            if href and "icon" in rel:
                _add_image(output, seen, public_url(urljoin(page.url, href)), "icon", page.url)
        for style in soup.find_all("style"):
            for url in css_url_values(style.get_text(), page.url):
                _add_image(output, seen, url, "css_background", page.url)
        for tag in soup.find_all(True):
            inline = str(tag.get("style") or "")
            for url in css_url_values(inline, page.url):
                _add_image(output, seen, url, "css_background", page.url)
        for match in MARKDOWN_IMAGE_RE.finditer(page.markdown):
            _add_image(output, seen, public_url(urljoin(page.url, match.group(1))), "markdown", page.url)
    return output


def _urls_from_img(tag: Any, base_url: str) -> list[str]:
    urls: list[str] = []
    src = str(tag.get("src") or "").strip()
    if src:
        urls.append(public_url(urljoin(base_url, src)))
    urls.extend(_urls_from_srcset(str(tag.get("srcset") or ""), base_url))
    return urls


def _urls_from_srcset(srcset: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for part in srcset.split(","):
        url = part.strip().split(" ", 1)[0].strip()
        if url:
            urls.append(public_url(urljoin(base_url, url)))
    return urls


def _add_image(
    output: list[dict[str, str]],
    seen: set[str],
    url: str,
    kind: str,
    source_url: str,
) -> None:
    if url.startswith("data:") or url in seen:
        return
    output.append({"url": public_url(url), "kind": kind, "source_url": source_url})
    seen.add(url)


def _run_screenshot_command(
    *,
    binary: str,
    url: str,
    output_path: Path,
    viewport: str,
    full_page: bool,
    wait_for: str,
) -> dict[str, Any]:
    if shutil.which(binary) is None:
        raise ContextPackError(f"agent-browser executable not found: {binary}")
    command = [
        binary,
        "--json",
        "--timeout",
        "30",
        "--viewport",
        viewport,
        "open",
        url,
        "wait",
        wait_for,
        "screenshot",
        "png",
        "base64",
    ]
    if full_page:
        command.append("--full-page")
    # argv list, no shell; target validation and trust env gate happen before this call.
    result = subprocess.run(  # nosec B603
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    legacy_error = ""
    if result.returncode == 0:
        try:
            png = _png_from_agent_browser_stdout(result.stdout)
        except ContextPackError as err:
            legacy_error = str(err)
        else:
            output_path.write_bytes(png)
            return _screenshot_payload(
                url=url,
                output_path=output_path,
                viewport=viewport,
                full_page=full_page,
                png=png,
                command=[part if part != url else public_url(url) for part in command],
            )
    else:
        legacy_error = (result.stderr or result.stdout).strip()[:500]
    try:
        png, command = _run_agent_browser_batch_screenshot(
            binary=binary,
            url=url,
            output_path=output_path,
            viewport=viewport,
            full_page=full_page,
            wait_for=wait_for,
        )
    except ContextPackError as err:
        detail = f"{legacy_error}; batch fallback: {err}" if legacy_error else str(err)
        raise ContextPackError(
            f"agent-browser screenshot failed with status {result.returncode}: {detail}"
        ) from err
    return _screenshot_payload(
        url=url,
        output_path=output_path,
        viewport=viewport,
        full_page=full_page,
        png=png,
        command=command,
    )


def _run_agent_browser_batch_screenshot(
    *,
    binary: str,
    url: str,
    output_path: Path,
    viewport: str,
    full_page: bool,
    wait_for: str,
) -> tuple[bytes, list[str]]:
    width, height = _viewport_dimensions(viewport)
    wait_ms = {"domcontentloaded": "500", "load": "1000", "networkidle": "2000"}[wait_for]
    screenshot_command = ["screenshot", str(output_path)]
    if full_page:
        screenshot_command.append("--full")
    batch = [
        ["open", url],
        ["set", "viewport", str(width), str(height)],
        ["wait", wait_ms],
        screenshot_command,
    ]
    batch_json = json.dumps(batch, separators=(",", ":"))
    session = f"docpull-screenshot-{hashlib.sha256(url.encode('utf-8')).hexdigest()[:12]}"
    command = [binary, "--session", session, "batch", "--bail", "--json"]
    try:
        result = subprocess.run(  # nosec B603
            command,
            input=batch_json,
            check=False,
            capture_output=True,
            text=True,
            timeout=45,
        )
    except subprocess.TimeoutExpired as err:
        raise ContextPackError("agent-browser batch screenshot timed out after 45 seconds.") from err
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[:500]
        raise ContextPackError(
            f"agent-browser batch screenshot failed with status {result.returncode}: {detail}"
        )
    _validate_agent_browser_batch_stdout(result.stdout)
    if not output_path.exists():
        raise ContextPackError("agent-browser batch screenshot did not write the expected PNG path.")
    png = output_path.read_bytes()
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ContextPackError("agent-browser batch screenshot did not write PNG data.")
    public_batch = [[public_url(item) if item == url else item for item in step] for step in batch]
    return png, [*command, json.dumps(public_batch, separators=(",", ":"))]


def _viewport_dimensions(viewport: str) -> tuple[int, int]:
    match = VIEWPORT_RE.fullmatch(viewport)
    if not match:
        raise ContextPackError("viewport must use WIDTHxHEIGHT with positive integer dimensions.")
    return int(match.group("width")), int(match.group("height"))


def _validate_agent_browser_batch_stdout(stdout: str) -> None:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as err:
        raise ContextPackError("agent-browser batch screenshot did not return JSON output.") from err
    if not isinstance(payload, list):
        raise ContextPackError("agent-browser batch screenshot JSON was not a command result array.")
    for item in payload:
        if not isinstance(item, dict) or item.get("success") is not True:
            raise ContextPackError("agent-browser batch screenshot reported a failed command.")


def _screenshot_payload(
    *,
    url: str,
    output_path: Path,
    viewport: str,
    full_page: bool,
    png: bytes,
    command: list[str],
) -> dict[str, Any]:
    return {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "url": url,
        "path": artifact_ref(output_path.parent.parent, output_path),
        "viewport": viewport,
        "full_page": full_page,
        "bytes": len(png),
        "sha256": hashlib.sha256(png).hexdigest(),
        "content_type": "image/png",
        "command": command,
    }


def _png_from_agent_browser_stdout(stdout: str) -> bytes:
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError as err:
        raise ContextPackError("agent-browser screenshot did not return JSON output.") from err
    candidates = [
        payload.get("screenshot"),
        payload.get("png"),
        payload.get("base64"),
        payload.get("data", {}).get("screenshot") if isinstance(payload.get("data"), dict) else None,
        payload.get("result", {}).get("screenshot") if isinstance(payload.get("result"), dict) else None,
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            raw = candidate.split(",", 1)[1] if candidate.startswith("data:image/png;base64,") else candidate
            data = base64.b64decode(raw)
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                return data
    raise ContextPackError("agent-browser screenshot JSON did not contain PNG base64 data.")


def _placeholder_page(url: str) -> PageSnapshot:
    return PageSnapshot(
        url=public_url(url),
        title=public_url(url),
        html="",
        markdown=f"Screenshot target: {public_url(url)}",
        metadata={},
        extraction={"workflow": SCREENSHOT_WORKFLOW},
        source_type=SCREENSHOT_WORKFLOW,
    )


def _images_markdown(images: list[dict[str, Any]], pages: list[PageSnapshot]) -> str:
    lines = ["# Visual Assets", ""]
    if images:
        for item in images[:60]:
            lines.append(f"- {quote_markdown(str(item.get('kind')))}: {item.get('url')}")
    else:
        lines.append("- No image candidates found.")
    lines.append("")
    lines.append("## Evidence")
    for index, page in enumerate(pages, start=1):
        lines.append(f"- [S{index}] [{quote_markdown(page.title or page.url)}]({page.url})")
    return "\n".join(lines)


def _screenshot_markdown(screenshot: dict[str, Any]) -> str:
    return (
        "# Screenshot\n\n"
        f"- URL: {screenshot.get('url')}\n"
        f"- Path: `{screenshot.get('path')}`\n"
        f"- SHA-256: `{screenshot.get('sha256')}`\n"
    )


def _generated_at() -> str:
    from ..time_utils import utc_now_iso

    return utc_now_iso()
