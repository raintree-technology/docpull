"""Optional local browser rendering helpers."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import json
import os
import re
import shlex
import shutil
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

from .models.config import RenderConfig
from .security.url_validator import UrlValidator
from .time_utils import utc_now_iso

RENDERED_PAGE_SCHEMA_VERSION = 1
_RENDER_LOCAL_TARGET_ENV = "DOCPULL_RENDER_ALLOW_LOCAL_TARGETS"
_RENDER_TRUSTED_BROWSER_ENV = "DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS"


class RenderError(RuntimeError):
    """Base class for rendering failures."""


class RendererUnavailableError(RenderError):
    """Raised when a configured renderer backend is not available."""


@dataclass(frozen=True)
class CommandResult:
    """Completed shell-command result from a renderer backend."""

    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RenderedPage:
    """Rendered HTML plus non-secret diagnostics."""

    url: str
    html: bytes
    backend: str
    source: str = "agent-browser"
    diagnostics: dict[str, Any] = field(default_factory=dict)
    rendered_at: str = field(default_factory=utc_now_iso)

    @property
    def html_bytes(self) -> int:
        return len(self.html)

    @property
    def html_sha256(self) -> str:
        return hashlib.sha256(self.html).hexdigest()


@dataclass(frozen=True)
class RenderArtifact:
    """Files written by ``docpull render``."""

    page: RenderedPage
    html_path: Path
    sidecar_path: Path


class Renderer(Protocol):
    """Protocol implemented by render backends."""

    async def render(self, url: str, config: RenderConfig) -> RenderedPage:
        """Render ``url`` and return HTML with diagnostics."""
        ...


AgentBrowserRunner = Callable[[Sequence[str], float], Awaitable[CommandResult]]
CloudSandboxRunner = Callable[[Sequence[str], float], Awaitable[CommandResult]]
CloudSandboxBackend = Literal["vercel-sandbox", "e2b-sandbox"]
_CLOUD_RENDER_SENTINEL = "DOCPULL_RENDER_RESULT="
_CLOUD_COST_PER_MINUTE_USD: dict[CloudSandboxBackend, float] = {
    "vercel-sandbox": 0.02,
    "e2b-sandbox": 0.03,
}


def agent_browser_binary(binary: str | None = None) -> str:
    """Return the configured agent-browser executable name or path."""
    return binary or os.environ.get("DOCPULL_AGENT_BROWSER_BIN", "agent-browser")


def check_agent_browser_availability(binary: str | None = None) -> tuple[bool, str]:
    """Check whether the optional external agent-browser backend is available."""
    candidate = agent_browser_binary(binary)
    path = shutil.which(candidate)
    if path:
        return True, f"[OK] agent-browser backend ({path})"
    return (
        False,
        f"[WARN] agent-browser backend unavailable - {_missing_agent_browser_message(candidate)}",
    )


def vercel_sandbox_binary(binary: str | None = None) -> str:
    """Return the configured Vercel Sandbox CLI executable name or path."""
    return binary or os.environ.get("DOCPULL_VERCEL_SANDBOX_BIN", "sandbox")


def check_vercel_sandbox_availability(binary: str | None = None) -> tuple[bool, str]:
    """Check whether the optional Vercel Sandbox CLI backend is available."""
    candidate = vercel_sandbox_binary(binary)
    path = shutil.which(candidate)
    if not path:
        return (
            False,
            "[WARN] Vercel Sandbox backend unavailable - "
            f"{candidate!r} was not found on PATH. Install the Vercel Sandbox CLI, "
            "set DOCPULL_VERCEL_SANDBOX_BIN, or use another render backend.",
        )
    auth_hint = "uses Vercel CLI auth, VERCEL_OIDC_TOKEN, or VERCEL_TOKEN"
    return True, f"[OK] Vercel Sandbox backend ({path}; {auth_hint})"


def check_e2b_sandbox_availability() -> tuple[bool, str]:
    """Check whether the optional E2B Python SDK backend is available."""
    try:
        importlib.import_module("e2b")
    except ImportError:
        return (
            False,
            "[WARN] E2B Sandbox backend unavailable - install `docpull[e2b]` or `pip install e2b`.",
        )
    if not os.environ.get("E2B_API_KEY"):
        return (
            False,
            "[WARN] E2B Sandbox backend unavailable - set E2B_API_KEY.",
        )
    return True, "[OK] E2B Sandbox backend (Python SDK and E2B_API_KEY configured)"


def estimate_cloud_render_cost_usd(backend: CloudSandboxBackend, config: RenderConfig) -> float:
    """Return a conservative per-render cost estimate for local guardrails."""
    return round(_cloud_timeout_minutes(config) * _CLOUD_COST_PER_MINUTE_USD[backend], 4)


def check_render_backend_availability(
    backend: str,
    *,
    binary: str | None = None,
) -> tuple[bool, str]:
    """Check availability for one renderer backend."""
    if backend == "agent-browser":
        return check_agent_browser_availability(binary)
    if backend == "vercel-sandbox":
        return check_vercel_sandbox_availability(binary)
    if backend == "e2b-sandbox":
        return check_e2b_sandbox_availability()
    return False, f"[WARN] Unknown render backend: {backend}"


def build_agent_browser_command(
    url: str,
    config: RenderConfig,
    *,
    binary: str = "agent-browser",
) -> list[str]:
    """Build the shell command used by the agent-browser backend."""
    viewport = f"{config.viewport.width}x{config.viewport.height}"
    return [
        binary,
        "--json",
        "--timeout",
        str(int(config.timeout_seconds)),
        "--viewport",
        viewport,
        "open",
        url,
        "wait",
        config.wait_for,
        "get",
        "html",
        "html",
    ]


class AgentBrowserRenderer:
    """Renderer that shells out to ``agent-browser --json``."""

    def __init__(
        self,
        *,
        binary: str | None = None,
        runner: AgentBrowserRunner | None = None,
    ) -> None:
        self._binary = agent_browser_binary(binary)
        self._runner = runner or _run_subprocess

    async def render(self, url: str, config: RenderConfig) -> RenderedPage:
        allowed = effective_allowed_domains(url, config)
        _validate_render_target(url, allowed)
        _ensure_action_policy_restrictive(config)

        command = build_agent_browser_command(url, config, binary=self._binary)
        try:
            result = await self._runner(command, float(config.timeout_seconds))
        except FileNotFoundError as err:
            raise RendererUnavailableError(_missing_agent_browser_message(self._binary)) from err

        if result.returncode != 0:
            stderr = result.stderr.strip()
            detail = f": {stderr}" if stderr else ""
            raise RenderError(f"agent-browser exited with status {result.returncode}{detail}")

        payload = _parse_agent_browser_json(result.stdout)
        html_text = _extract_html(payload)
        if html_text is None:
            raise RenderError(
                "agent-browser did not return rendered HTML. Expected JSON with "
                "`html`, `content`, `data.html`, or `result.html`."
            )

        html = html_text.encode("utf-8")
        max_bytes = int(config.max_html_bytes)
        if len(html) > max_bytes:
            raise RenderError(
                f"Rendered HTML is {len(html)} bytes, above max_html_bytes={max_bytes}. "
                "Raise --render-max-html-bytes only for trusted targets."
            )

        diagnostics = _extract_diagnostics(payload)
        diagnostics.update(
            {
                "command": command,
                "allowed_domains": allowed,
                "stdout_bytes": len(result.stdout.encode("utf-8")),
                "stderr_bytes": len(result.stderr.encode("utf-8")),
            }
        )
        return RenderedPage(
            url=url,
            html=html,
            backend=config.backend,
            diagnostics=diagnostics,
        )


class VercelSandboxRenderer:
    """Renderer that runs agent-browser inside Vercel Sandbox through the CLI."""

    def __init__(
        self,
        *,
        binary: str | None = None,
        runner: CloudSandboxRunner | None = None,
    ) -> None:
        self._binary = vercel_sandbox_binary(binary)
        self._runner = runner or _run_subprocess

    async def render(self, url: str, config: RenderConfig) -> RenderedPage:
        allowed = effective_allowed_domains(url, config)
        _validate_render_target(url, allowed)
        _ensure_action_policy_restrictive(config)
        _ensure_cloud_budget("vercel-sandbox", config)

        command = build_cloud_agent_browser_command(url, config)
        full_command = [
            self._binary,
            "run",
            "--rm",
            "--runtime",
            config.vercel_runtime,
            "--timeout",
            _cloud_timeout(config),
            "--",
            "bash",
            "-lc",
            command,
        ]
        try:
            result = await self._runner(full_command, float(config.timeout_seconds) + 180.0)
        except (FileNotFoundError, RendererUnavailableError) as err:
            raise RendererUnavailableError(check_vercel_sandbox_availability(self._binary)[1]) from err
        if result.returncode != 0:
            raise RenderError(_cloud_command_error("Vercel Sandbox", result))
        return _rendered_page_from_cloud_stdout(
            url=url,
            config=config,
            backend="vercel-sandbox",
            stdout=result.stdout,
            extra_diagnostics={
                "provider": "vercel",
                "runtime": config.vercel_runtime,
                "result_transport": "stdout",
                "renderer": "agent-browser",
                "agent_browser_binary": config.cloud_agent_browser_binary,
                "agent_browser_install": config.cloud_agent_browser_install,
                "estimated_cost_usd": estimate_cloud_render_cost_usd("vercel-sandbox", config),
                "max_estimated_cost_usd": config.cloud_max_estimated_cost_usd,
                "stdout_bytes": len(result.stdout.encode("utf-8")),
                "stderr_bytes": len(result.stderr.encode("utf-8")),
            },
        )


class E2BSandboxRenderer:
    """Renderer that runs agent-browser inside an E2B sandbox through the Python SDK."""

    def __init__(self, *, sandbox_factory: Callable[[], Any] | None = None) -> None:
        self._sandbox_factory = sandbox_factory

    async def render(self, url: str, config: RenderConfig) -> RenderedPage:
        allowed = effective_allowed_domains(url, config)
        _validate_render_target(url, allowed)
        _ensure_action_policy_restrictive(config)
        _ensure_cloud_budget("e2b-sandbox", config)
        return await asyncio.to_thread(self._render_sync, url, config)

    def _render_sync(self, url: str, config: RenderConfig) -> RenderedPage:
        sandbox = self._create_sandbox(config)
        try:
            command = build_cloud_agent_browser_command(url, config)
            timeout_seconds = float(config.timeout_seconds) + 180.0
            try:
                result = sandbox.commands.run(command, timeout=timeout_seconds)
            except TypeError:
                result = sandbox.commands.run(command)
            stdout = str(getattr(result, "stdout", "") or "")
            stderr = str(getattr(result, "stderr", "") or "")
            exit_code = int(getattr(result, "exit_code", getattr(result, "returncode", 0)) or 0)
            if exit_code != 0:
                raise RenderError(
                    _cloud_command_error(
                        "E2B Sandbox",
                        CommandResult(returncode=exit_code, stdout=stdout, stderr=stderr),
                    )
                )
            payload: Any | None = None
            result_transport = _cloud_result_transport("e2b-sandbox", config)
            file_transport_error: str | None = None
            if result_transport == "file":
                try:
                    payload = _parse_cloud_render_artifact(
                        _read_e2b_file(sandbox, config.cloud_artifact_path, timeout_seconds=timeout_seconds)
                    )
                except RenderError as err:
                    if config.cloud_result_transport == "file":
                        raise
                    file_transport_error = str(err)
            return _rendered_page_from_cloud_payload(
                url=url,
                config=config,
                backend="e2b-sandbox",
                payload=payload if payload is not None else _parse_cloud_render_payload(stdout),
                extra_diagnostics={
                    "provider": "e2b",
                    "sandbox_id": str(getattr(sandbox, "sandbox_id", "")) or None,
                    "template": config.e2b_template or os.environ.get("DOCPULL_E2B_TEMPLATE"),
                    "result_transport": "file" if payload is not None else "stdout",
                    "file_transport_error": file_transport_error,
                    "artifact_path": config.cloud_artifact_path,
                    "renderer": "agent-browser",
                    "agent_browser_binary": config.cloud_agent_browser_binary,
                    "agent_browser_install": config.cloud_agent_browser_install,
                    "estimated_cost_usd": estimate_cloud_render_cost_usd("e2b-sandbox", config),
                    "max_estimated_cost_usd": config.cloud_max_estimated_cost_usd,
                    "stdout_bytes": len(stdout.encode("utf-8")),
                    "stderr_bytes": len(stderr.encode("utf-8")),
                },
            )
        finally:
            kill = getattr(sandbox, "kill", None)
            if callable(kill):
                kill()

    def _create_sandbox(self, config: RenderConfig) -> Any:
        if self._sandbox_factory is not None:
            return self._sandbox_factory()
        if not os.environ.get("E2B_API_KEY"):
            raise RendererUnavailableError("E2B Sandbox requires E2B_API_KEY.")
        try:
            e2b = importlib.import_module("e2b")
        except ImportError as err:
            raise RendererUnavailableError(
                "E2B Sandbox requires the `e2b` Python package. Install `docpull[e2b]` or `pip install e2b`."
            ) from err
        sandbox_class = getattr(e2b, "Sandbox", None)
        if sandbox_class is None:
            raise RendererUnavailableError("The installed `e2b` package does not expose Sandbox.")
        timeout_seconds = float(config.timeout_seconds) + 180.0
        template = config.e2b_template or os.environ.get("DOCPULL_E2B_TEMPLATE")
        create = getattr(sandbox_class, "create", None)
        if callable(create):
            return _call_e2b_create(create, template=template, timeout_seconds=timeout_seconds)
        return _call_e2b_create(sandbox_class, template=template, timeout_seconds=timeout_seconds)


async def render_url(
    url: str,
    *,
    config: RenderConfig | dict[str, Any] | str | None = None,
    renderer: Renderer | None = None,
) -> RenderedPage:
    """Render a URL with the configured backend."""
    render_config = _coerce_render_config(config)
    if not render_config.enabled:
        raise RenderError("Rendering is disabled. Set render='agent-browser' or render='fallback'.")
    backend = renderer or create_renderer(render_config)
    return await backend.render(url, render_config)


async def render_url_to_directory(
    url: str,
    output_dir: Path,
    *,
    config: RenderConfig | dict[str, Any] | str | None = None,
    renderer: Renderer | None = None,
) -> RenderArtifact:
    """Render a URL, save HTML, and append ``rendered_pages.ndjson``."""
    render_config = _coerce_render_config(config or "agent-browser")
    page = await render_url(url, config=render_config, renderer=renderer)
    output_dir.mkdir(parents=True, exist_ok=True)
    html_path = output_dir / _url_to_html_filename(url)
    html_path.write_bytes(page.html)
    sidecar_path = append_rendered_page_record(
        output_dir,
        page,
        render_config,
        source="docpull_render_cli",
        artifact_path=html_path,
    )
    return RenderArtifact(page=page, html_path=html_path, sidecar_path=sidecar_path)


def create_renderer(config: RenderConfig) -> Renderer:
    """Create the renderer backend for ``config``."""
    if config.backend == "agent-browser":
        return AgentBrowserRenderer()
    if config.backend == "vercel-sandbox":
        return VercelSandboxRenderer()
    if config.backend == "e2b-sandbox":
        return E2BSandboxRenderer()
    raise RenderError(f"Unsupported render backend: {config.backend}")


def render_metadata(page: RenderedPage, config: RenderConfig) -> dict[str, Any]:
    """Return metadata stored on rendered ``DocumentRecord`` objects."""
    return {
        "rendered": True,
        "backend": page.backend,
        "source": page.source,
        "rendered_at": page.rendered_at,
        "html_sha256": page.html_sha256,
        "html_bytes": page.html_bytes,
        "mode": config.mode,
        "wait_for": config.wait_for,
        "timeout_seconds": config.timeout_seconds,
        "viewport": config.viewport.model_dump(mode="json"),
        "allowed_domains": effective_allowed_domains(page.url, config),
        "action_policy": config.action_policy.model_dump(mode="json"),
        "diagnostics": page.diagnostics,
    }


def build_cloud_agent_browser_command(url: str, config: RenderConfig) -> str:
    """Build a self-contained command that renders a URL with agent-browser in a sandbox."""
    script = _cloud_agent_browser_script(url, config)
    encoded_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
    lines = ["set -euo pipefail", "# renderer: agent-browser"]
    binary = shlex.quote(config.cloud_agent_browser_binary)
    if config.cloud_agent_browser_install == "auto":
        lines.extend(
            [
                f"if ! command -v {binary} >/dev/null 2>&1; then npm install -g agent-browser; fi",
                f"{binary} install >/tmp/docpull-agent-browser-install.log 2>&1",
            ]
        )
    lines.extend(
        [
            "node - <<'NODE'",
            f"const source = Buffer.from({encoded_script!r}, 'base64').toString('utf8');",
            "eval(source);",
            "NODE",
        ]
    )
    return "\n".join(lines)


def _cloud_agent_browser_script(url: str, config: RenderConfig) -> str:
    command = build_agent_browser_command(
        url,
        config,
        binary=config.cloud_agent_browser_binary,
    )
    max_buffer_bytes = int(config.max_html_bytes) + (1024 * 1024)
    return f"""
const fs = require("fs");
const path = require("path");
const childProcess = require("child_process");

const URL = {json.dumps(url)};
const WAIT_FOR = {json.dumps(config.wait_for)};
const TIMEOUT_SECONDS = {float(config.timeout_seconds)};
const MAX_BUFFER_BYTES = {max_buffer_bytes};
const SENTINEL = {json.dumps(_CLOUD_RENDER_SENTINEL)};
const ALLOWED_DOMAINS = {json.dumps(effective_allowed_domains(url, config))};
const RESULT_PATH = {json.dumps(config.cloud_artifact_path)};
const COMMAND = {json.dumps(command)};

function extractHtml(payload) {{
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {{
    for (const key of ["html", "content"]) {{
      const value = payload[key];
      if (typeof value === "string") {{
        return value;
      }}
    }}
    for (const key of ["data", "result", "page"]) {{
      const found = extractHtml(payload[key]);
      if (found !== null) {{
        return found;
      }}
    }}
  }}
  if (Array.isArray(payload)) {{
    for (const item of payload) {{
      const found = extractHtml(item);
      if (found !== null) {{
        return found;
      }}
    }}
  }}
  return null;
}}

function emitPayload(payload) {{
  fs.mkdirSync(path.dirname(RESULT_PATH), {{ recursive: true }});
  const serialized = JSON.stringify(payload);
  fs.writeFileSync(RESULT_PATH, serialized, "utf8");
  const encoded = Buffer.from(serialized, "utf8").toString("base64");
  console.log(SENTINEL + encoded);
}}

function parseAgentBrowserStdout(stdout) {{
  const text = stdout.trim();
  if (!text) {{
    throw new Error("agent-browser returned empty stdout");
  }}
  try {{
    return JSON.parse(text);
  }} catch (error) {{
    if (text.trimStart().startsWith("<")) {{
      return {{ html: text }};
    }}
    throw error;
  }}
}}

function errorMessage(error) {{
  if (!error) {{
    return "unknown error";
  }}
  if (error.message) {{
    return String(error.message);
  }}
  return String(error);
}}

function main() {{
  const diagnostics = {{
    renderer: "agent-browser",
    target_url: URL,
    wait_for: WAIT_FOR,
    allowed_domains: ALLOWED_DOMAINS,
    command: COMMAND,
  }};
  try {{
    const result = childProcess.spawnSync(COMMAND[0], COMMAND.slice(1), {{
      encoding: "utf8",
      timeout: Math.ceil(TIMEOUT_SECONDS * 1000),
      maxBuffer: MAX_BUFFER_BYTES,
    }});
    const stdout = result.stdout || "";
    const stderr = result.stderr || "";
    const status = typeof result.status === "number" ? result.status : 1;
    diagnostics.returncode = status;
    diagnostics.signal = result.signal || null;
    diagnostics.stdout_bytes = Buffer.byteLength(stdout, "utf8");
    diagnostics.stderr_bytes = Buffer.byteLength(stderr, "utf8");
    if (stderr) {{
      diagnostics.stderr_tail = stderr.slice(-1000);
    }}
    if (result.error) {{
      throw result.error;
    }}
    if (status !== 0) {{
      throw new Error(`agent-browser exited with status ${{status}}`);
    }}
    const rawPayload = parseAgentBrowserStdout(stdout);
    const html = extractHtml(rawPayload);
    if (html === null) {{
      throw new Error("agent-browser JSON did not contain rendered HTML");
    }}
    emitPayload({{ html, diagnostics }});
  }} catch (error) {{
    diagnostics.error = errorMessage(error);
    if (error && error.stack) {{
      diagnostics.stack = String(error.stack).split("\\n").slice(0, 4).join("\\n");
    }}
    emitPayload({{ diagnostics }});
    process.exitCode = 1;
  }}
}}

main();
""".strip()


def _rendered_page_from_cloud_stdout(
    *,
    url: str,
    config: RenderConfig,
    backend: CloudSandboxBackend,
    stdout: str,
    extra_diagnostics: dict[str, Any],
) -> RenderedPage:
    return _rendered_page_from_cloud_payload(
        url=url,
        config=config,
        backend=backend,
        payload=_parse_cloud_render_payload(stdout),
        extra_diagnostics=extra_diagnostics,
    )


def _rendered_page_from_cloud_payload(
    *,
    url: str,
    config: RenderConfig,
    backend: CloudSandboxBackend,
    payload: Any,
    extra_diagnostics: dict[str, Any],
) -> RenderedPage:
    html_text = _extract_html(payload)
    if html_text is None:
        raise RenderError(
            f"{backend} did not return rendered HTML. Expected a cloud render payload with `html`."
        )
    html = html_text.encode("utf-8")
    max_bytes = int(config.max_html_bytes)
    if len(html) > max_bytes:
        raise RenderError(
            f"Rendered HTML is {len(html)} bytes, above max_html_bytes={max_bytes}. "
            "Raise --render-max-html-bytes only for trusted targets."
        )
    diagnostics = _extract_diagnostics(payload)
    diagnostics.update({key: value for key, value in extra_diagnostics.items() if value is not None})
    return RenderedPage(
        url=url,
        html=html,
        backend=backend,
        source=backend,
        diagnostics=diagnostics,
    )


def _parse_cloud_render_payload(stdout: str) -> Any:
    for line in reversed(stdout.splitlines()):
        if not line.startswith(_CLOUD_RENDER_SENTINEL):
            continue
        encoded = line[len(_CLOUD_RENDER_SENTINEL) :].strip()
        try:
            return json.loads(base64.b64decode(encoded).decode("utf-8"))
        except (ValueError, json.JSONDecodeError) as err:
            raise RenderError(f"Cloud sandbox returned invalid render payload: {err}") from err
    raise RenderError(f"Cloud sandbox output did not include {_CLOUD_RENDER_SENTINEL!r}.")


def _parse_cloud_render_artifact(content: str) -> Any:
    try:
        return json.loads(content)
    except json.JSONDecodeError as err:
        raise RenderError(f"Cloud sandbox artifact contained invalid JSON: {err}") from err


def _read_e2b_file(sandbox: Any, path: str, *, timeout_seconds: float) -> str:
    files = getattr(sandbox, "files", None)
    read = getattr(files, "read", None)
    if not callable(read):
        raise RenderError("E2B SDK object does not expose files.read for file result transport.")
    try:
        value = read(path, request_timeout=timeout_seconds)
    except TypeError:
        value = read(path)
    if isinstance(value, bytes | bytearray):
        return bytes(value).decode("utf-8")
    return str(value)


def _call_e2b_create(create: Callable[..., Any], *, template: str | None, timeout_seconds: float) -> Any:
    attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    if template:
        attempts.extend(
            [
                ((template,), {"timeout": timeout_seconds}),
                ((), {"template": template, "timeout": timeout_seconds}),
                ((template,), {}),
                ((), {"template": template}),
            ]
        )
    attempts.extend(
        [
            ((), {"timeout": timeout_seconds}),
            ((), {}),
        ]
    )
    last_error: TypeError | None = None
    for args, kwargs in attempts:
        try:
            return create(*args, **kwargs)
        except TypeError as err:
            last_error = err
            continue
    if last_error is not None:
        raise last_error
    return create()


def _ensure_cloud_budget(backend: CloudSandboxBackend, config: RenderConfig) -> None:
    if config.cloud_max_estimated_cost_usd is None:
        return
    estimate = estimate_cloud_render_cost_usd(backend, config)
    if estimate > config.cloud_max_estimated_cost_usd:
        raise RenderError(
            f"{backend} estimated render cost ${estimate:.4f} exceeds "
            f"cloud_max_estimated_cost_usd=${config.cloud_max_estimated_cost_usd:.4f}."
        )


def _cloud_result_transport(backend: CloudSandboxBackend, config: RenderConfig) -> Literal["stdout", "file"]:
    if config.cloud_result_transport == "stdout":
        return "stdout"
    if config.cloud_result_transport == "file":
        return "file"
    return "file" if backend == "e2b-sandbox" else "stdout"


def _cloud_timeout(config: RenderConfig) -> str:
    return f"{_cloud_timeout_minutes(config)}m"


def _cloud_timeout_minutes(config: RenderConfig) -> int:
    seconds = int(float(config.timeout_seconds) + 180.0)
    return max(1, (seconds + 59) // 60)


def _cloud_command_error(label: str, result: CommandResult) -> str:
    stderr = " ".join(result.stderr.split())
    stdout = " ".join(result.stdout.split())
    detail = stderr or stdout
    if len(detail) > 500:
        detail = detail[:497] + "..."
    return f"{label} render command exited with status {result.returncode}: {detail}"


def append_rendered_page_record(
    output_dir: Path,
    page: RenderedPage,
    config: RenderConfig,
    *,
    source: str,
    artifact_path: Path | None = None,
) -> Path:
    """Append one rendered-page provenance record to ``rendered_pages.ndjson``."""
    output_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = output_dir / "rendered_pages.ndjson"
    record = {
        "schema_version": RENDERED_PAGE_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "url": page.url,
        "source": source,
        "backend": page.backend,
        "rendered_at": page.rendered_at,
        "html_sha256": page.html_sha256,
        "html_bytes": page.html_bytes,
        "mode": config.mode,
        "wait_for": config.wait_for,
        "timeout_seconds": config.timeout_seconds,
        "viewport": config.viewport.model_dump(mode="json"),
        "allowed_domains": effective_allowed_domains(page.url, config),
        "action_policy": config.action_policy.model_dump(mode="json"),
        "diagnostics": page.diagnostics,
    }
    if artifact_path is not None:
        try:
            record["artifact_path"] = str(artifact_path.resolve().relative_to(output_dir.resolve()))
        except ValueError:
            record["artifact_path"] = str(artifact_path)
    with sidecar_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return sidecar_path


def effective_allowed_domains(url: str, config: RenderConfig) -> list[str]:
    """Return configured domains or a narrow one-host default from ``url``."""
    if config.allowed_domains:
        return [domain.lower().rstrip(".") for domain in config.allowed_domains]
    host = urlparse(url).hostname
    if not host:
        raise RenderError(f"Cannot derive render allowed domain from URL: {url}")
    return [host.lower().rstrip(".")]


def _ensure_url_allowed(url: str, allowed_domains: Sequence[str]) -> None:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if not host:
        raise RenderError(f"Cannot render URL without a hostname: {url}")
    if scheme != "https" and not (scheme == "http" and _is_loopback_host(host)):
        raise RenderError("Render URL must use HTTPS unless targeting localhost or loopback HTTP.")
    normalized = host.lower().rstrip(".")
    if normalized in allowed_domains:
        return
    # A leading-dot allow-list entry intentionally covers subdomains.
    if any(domain.startswith(".") and normalized.endswith(domain) for domain in allowed_domains):
        return
    raise RenderError(
        f"Render blocked for {url}: host {normalized!r} is outside allowed_domains={list(allowed_domains)!r}."
    )


def _validate_render_target(url: str, allowed_domains: Sequence[str]) -> None:
    _ensure_url_allowed(url, allowed_domains)
    parsed = urlparse(url)
    host = parsed.hostname
    if host and _local_render_override_enabled(parsed.scheme.lower(), host):
        return

    result = UrlValidator(allowed_schemes={"https"}).validate(url)
    if not result.is_valid:
        raise RenderError(f"Render URL validation failed for {url}: {result.rejection_reason}")
    if os.environ.get(_RENDER_TRUSTED_BROWSER_ENV) != "1":
        raise RenderError(
            "Browser rendering is disabled for untrusted network targets because the current "
            "agent-browser backend cannot enforce redirect, subresource, or connect-time DNS "
            f"allow-lists. Set {_RENDER_TRUSTED_BROWSER_ENV}=1 only for trusted targets."
        )


def _local_render_override_enabled(scheme: str, host: str) -> bool:
    return scheme == "http" and _is_loopback_host(host) and os.environ.get(_RENDER_LOCAL_TARGET_ENV) == "1"


def _ensure_action_policy_restrictive(config: RenderConfig) -> None:
    enabled = [name for name, value in config.action_policy.model_dump().items() if value]
    if enabled:
        raise RenderError(
            "Browser rendering only supports the default restrictive action policy; "
            f"unsupported enabled flags: {', '.join(sorted(enabled))}."
        )


def _is_loopback_host(host: str) -> bool:
    normalized = host.lower().rstrip(".")
    return normalized == "localhost" or normalized == "::1" or normalized.startswith("127.")


async def _run_subprocess(command: Sequence[str], timeout_seconds: float) -> CommandResult:
    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as err:
        binary = command[0] if command else "agent-browser"
        raise RendererUnavailableError(_missing_agent_browser_message(binary)) from err

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as err:
        process.kill()
        await process.communicate()
        raise RenderError(f"agent-browser timed out after {timeout_seconds:g}s") from err

    return CommandResult(
        returncode=process.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


def _missing_agent_browser_message(binary: str) -> str:
    return (
        f"{binary!r} was not found on PATH. Install agent-browser, set "
        "DOCPULL_AGENT_BROWSER_BIN to its executable path, or rerun with --render off."
    )


def _parse_agent_browser_json(stdout: str) -> Any:
    text = stdout.strip()
    if not text:
        raise RenderError("agent-browser returned empty stdout")
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        if text.lstrip().startswith("<"):
            return {"html": text}
        raise RenderError("agent-browser returned non-JSON output despite --json") from err


def _extract_html(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("html", "content"):
            value = payload.get(key)
            if isinstance(value, str):
                return value
        for key in ("data", "result", "page"):
            nested = payload.get(key)
            found = _extract_html(nested)
            if found is not None:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _extract_html(item)
            if found is not None:
                return found
    return None


def _extract_diagnostics(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    diagnostics = payload.get("diagnostics")
    if isinstance(diagnostics, dict):
        return dict(diagnostics)
    meta = payload.get("metadata")
    if isinstance(meta, dict):
        return {"metadata": meta}
    return {}


def _coerce_render_config(config: RenderConfig | dict[str, Any] | str | None) -> RenderConfig:
    if isinstance(config, RenderConfig):
        return config
    if config is None:
        return RenderConfig()
    return RenderConfig.model_validate(config)


_FILENAME_SAFE_RE = re.compile(r"[^\w\-.]+")


def _url_to_html_filename(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname or "url"
    if parsed.port:
        host = f"{host}-{parsed.port}"
    host = _FILENAME_SAFE_RE.sub("_", host.lower().rstrip(".")).strip("._") or "url"
    path = parsed.path.strip("/")
    if not path:
        stem = "index"
    else:
        stem = "_".join(part for part in path.split("/") if part) or "index"
        stem = stem.rsplit(".", 1)[0] if stem.endswith((".html", ".htm")) else stem
    stem = _FILENAME_SAFE_RE.sub("_", stem).strip("._") or "index"
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    return f"{host}_{stem[:80]}_{digest}.html"
