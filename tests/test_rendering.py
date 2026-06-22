"""Tests for optional local rendering."""

from __future__ import annotations

import base64
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import docpull.rendering as rendering
from docpull.cli import main
from docpull.models.config import RenderActionPolicy, RenderConfig
from docpull.rendering import (
    AgentBrowserRenderer,
    CommandResult,
    E2BSandboxRenderer,
    RenderError,
    RendererUnavailableError,
    VercelSandboxRenderer,
    agent_browser_binary,
    build_agent_browser_command,
    build_cloud_agent_browser_command,
    check_agent_browser_availability,
    check_e2b_sandbox_availability,
    check_render_backend_availability,
    check_vercel_sandbox_availability,
    estimate_cloud_render_cost_usd,
    render_url_to_directory,
)


def test_agent_browser_binary_prefers_explicit_value_and_env(monkeypatch):
    monkeypatch.setenv("DOCPULL_AGENT_BROWSER_BIN", "/opt/bin/custom-agent-browser")

    assert agent_browser_binary() == "/opt/bin/custom-agent-browser"
    assert agent_browser_binary("/tmp/agent-browser") == "/tmp/agent-browser"


def test_check_agent_browser_availability_reports_path(monkeypatch):
    monkeypatch.setattr(
        rendering.shutil,
        "which",
        lambda binary: "/opt/bin/agent-browser" if binary == "agent-browser" else None,
    )

    assert check_agent_browser_availability() == (
        True,
        "[OK] agent-browser backend (/opt/bin/agent-browser)",
    )


def test_check_agent_browser_availability_reports_missing(monkeypatch):
    monkeypatch.setattr(rendering.shutil, "which", lambda _binary: None)

    available, message = check_agent_browser_availability("missing-agent-browser")

    assert available is False
    assert "[WARN] agent-browser backend unavailable" in message
    assert "DOCPULL_AGENT_BROWSER_BIN" in message


def test_check_vercel_sandbox_availability_reports_cli(monkeypatch):
    monkeypatch.setattr(
        rendering.shutil,
        "which",
        lambda binary: "/opt/bin/sandbox" if binary == "sandbox" else None,
    )

    available, message = check_vercel_sandbox_availability()

    assert available is True
    assert "Vercel Sandbox backend" in message


def test_check_e2b_sandbox_availability_reports_missing_key(monkeypatch):
    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.setattr(rendering.importlib, "import_module", lambda _name: object())

    available, message = check_e2b_sandbox_availability()

    assert available is False
    assert "E2B_API_KEY" in message


def test_check_render_backend_availability_dispatches(monkeypatch):
    monkeypatch.setattr(
        rendering,
        "check_vercel_sandbox_availability",
        lambda binary=None: (True, f"vercel:{binary}"),
    )

    assert check_render_backend_availability("vercel-sandbox", binary="/bin/sandbox") == (
        True,
        "vercel:/bin/sandbox",
    )


def test_agent_browser_command_construction():
    config = RenderConfig(
        mode="agent-browser",
        timeout_seconds=12,
        wait_for="networkidle",
        viewport="800x600",
    )

    command = build_agent_browser_command(
        "https://example.com/app",
        config,
        binary="/opt/bin/agent-browser",
    )

    assert command == [
        "/opt/bin/agent-browser",
        "--json",
        "--timeout",
        "12",
        "--viewport",
        "800x600",
        "open",
        "https://example.com/app",
        "wait",
        "networkidle",
        "get",
        "html",
        "html",
    ]


def test_cloud_agent_browser_install_can_be_skipped_for_prebuilt_template():
    config = RenderConfig(
        mode="agent-browser",
        backend="e2b-sandbox",
        cloud_agent_browser_install="skip",
        allowed_domains=["example.com"],
    )

    command = build_cloud_agent_browser_command("https://example.com/app", config)
    script = rendering._cloud_agent_browser_script("https://example.com/app", config)

    assert "npm install -g agent-browser" not in command
    assert "node - <<'NODE'" in command
    assert 'const ALLOWED_DOMAINS = ["example.com"];' in script
    assert '"agent-browser", "--json"' in script


def test_render_config_runtime_alias_maps_to_backend():
    config = RenderConfig(mode="agent-browser", runtime="vercel")

    assert config.backend == "vercel-sandbox"


def test_estimated_cloud_render_cost_is_duration_based():
    config = RenderConfig(mode="agent-browser", backend="vercel-sandbox", timeout_seconds=30)

    assert estimate_cloud_render_cost_usd("vercel-sandbox", config) == 0.08


def test_render_cli_budget_zero_blocks_cloud_runtime(tmp_path, capsys):
    assert (
        main(
            [
                "render",
                "https://example.com/app",
                "--runtime",
                "e2b",
                "--budget",
                "0",
                "--output-dir",
                str(tmp_path / "rendered"),
            ]
        )
        == 1
    )

    output = capsys.readouterr().out
    assert "Budget error" in output
    accounting = tmp_path / "rendered" / "run.accounting.json"
    assert accounting.exists()
    payload = json.loads(accounting.read_text(encoding="utf-8"))
    assert payload["budget_limit_usd"] == 0
    assert payload["blocked_actions"][0]["provider"] == "e2b-sandbox"


@pytest.mark.asyncio
async def test_agent_browser_missing_binary_error_is_actionable():
    async def missing_runner(command, timeout):
        raise FileNotFoundError(command[0])

    renderer = AgentBrowserRenderer(binary="missing-agent-browser", runner=missing_runner)

    with pytest.raises(RendererUnavailableError, match="DOCPULL_AGENT_BROWSER_BIN"):
        await renderer.render("https://example.com", RenderConfig(mode="agent-browser"))


@pytest.mark.asyncio
async def test_render_blocks_url_outside_allowed_domains():
    async def runner(command, timeout):
        raise AssertionError("renderer should not be called")

    renderer = AgentBrowserRenderer(runner=runner)

    with pytest.raises(RenderError, match="outside allowed_domains"):
        await renderer.render(
            "https://evil.example.com/app",
            RenderConfig(mode="agent-browser", allowed_domains=["docs.example.com"]),
        )


@pytest.mark.asyncio
async def test_render_blocks_public_http_url():
    async def runner(command, timeout):
        raise AssertionError("renderer should not be called")

    renderer = AgentBrowserRenderer(runner=runner)

    with pytest.raises(RenderError, match="must use HTTPS"):
        await renderer.render(
            "http://example.com/app",
            RenderConfig(mode="agent-browser", allowed_domains=["example.com"]),
        )


@pytest.mark.asyncio
async def test_render_blocks_non_restrictive_action_policy():
    async def runner(command, timeout):
        raise AssertionError("renderer should not be called")

    renderer = AgentBrowserRenderer(runner=runner)

    with pytest.raises(RenderError, match="default restrictive action policy"):
        await renderer.render(
            "https://example.com/app",
            RenderConfig(
                mode="agent-browser",
                action_policy=RenderActionPolicy(allow_download=True),
            ),
        )


@pytest.mark.asyncio
async def test_render_url_to_directory_writes_html_and_sidecar(tmp_path):
    async def runner(command, timeout):
        return CommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "result": {
                        "html": "<html><body><h1>Rendered</h1></body></html>",
                    },
                    "diagnostics": {"loaded": True},
                }
            ),
            stderr="",
        )

    renderer = AgentBrowserRenderer(runner=runner)

    artifact = await render_url_to_directory(
        "https://example.com/app",
        tmp_path,
        config=RenderConfig(mode="agent-browser"),
        renderer=renderer,
    )

    assert artifact.html_path.name == "app.html"
    assert artifact.html_path.read_text(encoding="utf-8") == "<html><body><h1>Rendered</h1></body></html>"
    records = [json.loads(line) for line in artifact.sidecar_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["schema_version"] == 1
    assert record["url"] == "https://example.com/app"
    assert record["source"] == "docpull_render_cli"
    assert record["backend"] == "agent-browser"
    assert record["artifact_path"] == "app.html"
    assert record["diagnostics"]["loaded"] is True
    assert record["allowed_domains"] == ["example.com"]


@pytest.mark.asyncio
async def test_vercel_sandbox_renderer_uses_cli_and_parses_payload():
    captured: dict[str, object] = {}

    async def runner(command, timeout):
        captured["command"] = list(command)
        captured["timeout"] = timeout
        return CommandResult(
            returncode=0,
            stdout=_cloud_stdout("<html><body><h1>Cloud</h1></body></html>"),
            stderr="",
        )

    renderer = VercelSandboxRenderer(binary="/opt/bin/sandbox", runner=runner)
    page = await renderer.render(
        "https://example.com/app",
        RenderConfig(mode="agent-browser", backend="vercel-sandbox"),
    )

    command = captured["command"]
    assert isinstance(command, list)
    assert command[:6] == ["/opt/bin/sandbox", "run", "--rm", "--runtime", "node22", "--timeout"]
    assert command[6] == "4m"
    assert page.backend == "vercel-sandbox"
    assert page.source == "vercel-sandbox"
    assert b"<h1>Cloud</h1>" in page.html
    assert page.diagnostics["provider"] == "vercel"
    assert page.diagnostics["estimated_cost_usd"] == 0.08


@pytest.mark.asyncio
async def test_cloud_render_budget_cap_blocks_before_provider_call():
    async def runner(command, timeout):
        raise AssertionError("renderer should not be called")

    renderer = VercelSandboxRenderer(binary="/opt/bin/sandbox", runner=runner)

    with pytest.raises(RenderError, match="estimated render cost"):
        await renderer.render(
            "https://example.com/app",
            RenderConfig(
                mode="agent-browser",
                backend="vercel-sandbox",
                cloud_max_estimated_cost_usd=0.01,
            ),
        )


@pytest.mark.asyncio
async def test_e2b_sandbox_renderer_runs_command_and_kills_sandbox():
    class FakeResult:
        stdout = _cloud_stdout("<html><body><h1>E2B</h1></body></html>")
        stderr = ""
        exit_code = 0

    class FakeCommands:
        def __init__(self):
            self.command = ""
            self.timeout = None

        def run(self, command, timeout=None):
            self.command = command
            self.timeout = timeout
            return FakeResult()

    class FakeSandbox:
        def __init__(self):
            self.commands = FakeCommands()
            self.sandbox_id = "sbx_test"
            self.killed = False

        def kill(self):
            self.killed = True

    sandbox = FakeSandbox()
    renderer = E2BSandboxRenderer(sandbox_factory=lambda: sandbox)

    page = await renderer.render(
        "https://example.com/app",
        RenderConfig(mode="agent-browser", backend="e2b-sandbox"),
    )

    assert "agent-browser" in sandbox.commands.command
    assert "python3 -m pip install" not in sandbox.commands.command
    assert sandbox.commands.timeout == 210.0
    assert sandbox.killed is True
    assert page.backend == "e2b-sandbox"
    assert page.diagnostics["provider"] == "e2b"
    assert page.diagnostics["sandbox_id"] == "sbx_test"
    assert page.diagnostics["result_transport"] == "stdout"
    assert page.diagnostics["file_transport_error"]
    assert b"<h1>E2B</h1>" in page.html


@pytest.mark.asyncio
async def test_e2b_sandbox_renderer_prefers_file_transport():
    class FakeResult:
        stdout = "no sentinel here"
        stderr = ""
        exit_code = 0

    class FakeCommands:
        def run(self, command, timeout=None):
            return FakeResult()

    class FakeFiles:
        def __init__(self):
            self.path = ""
            self.timeout = None

        def read(self, path, request_timeout=None):
            self.path = path
            self.timeout = request_timeout
            return json.dumps(
                {
                    "html": "<html><body><h1>From file</h1></body></html>",
                    "diagnostics": {"loaded": True},
                }
            )

    class FakeSandbox:
        def __init__(self):
            self.commands = FakeCommands()
            self.files = FakeFiles()

        def kill(self):
            pass

    sandbox = FakeSandbox()
    renderer = E2BSandboxRenderer(sandbox_factory=lambda: sandbox)

    page = await renderer.render(
        "https://example.com/app",
        RenderConfig(
            mode="agent-browser",
            backend="e2b-sandbox",
            cloud_result_transport="file",
            cloud_artifact_path="/tmp/result.json",
        ),
    )

    assert sandbox.files.path == "/tmp/result.json"
    assert sandbox.files.timeout == 210.0
    assert page.diagnostics["result_transport"] == "file"
    assert b"<h1>From file</h1>" in page.html


def test_e2b_create_uses_template_and_seconds_timeout():
    calls = []

    def create(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    rendering._call_e2b_create(create, template="docpull-agent-browser", timeout_seconds=210.0)

    assert calls == [(("docpull-agent-browser",), {"timeout": 210.0})]


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["vercel-sandbox", "e2b-sandbox"])
async def test_live_cloud_render_backend_when_enabled(backend):
    if os.environ.get("DOCPULL_LIVE_CLOUD_RENDER") != "1":
        pytest.skip("Set DOCPULL_LIVE_CLOUD_RENDER=1 to run live cloud renderer smoke tests.")
    available, message = check_render_backend_availability(backend)
    if not available:
        pytest.skip(message)

    renderer = VercelSandboxRenderer() if backend == "vercel-sandbox" else E2BSandboxRenderer()
    page = await renderer.render(
        "https://example.com",
        RenderConfig(
            mode="agent-browser",
            backend=backend,
            timeout_seconds=20,
            allowed_domains=["example.com"],
            cloud_max_estimated_cost_usd=0.20,
        ),
    )

    assert b"Example Domain" in page.html


@pytest.mark.asyncio
async def test_live_agent_browser_backend_executes_js_when_available():
    available, message = check_agent_browser_availability()
    if not available:
        pytest.skip(message)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = (
                b"<html><body><div id='root'>Loading</div>"
                b"<script>document.getElementById('root').textContent='Rendered by JS';</script>"
                b"</body></html>"
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/"
        page = await AgentBrowserRenderer().render(
            url,
            RenderConfig(mode="agent-browser", allowed_domains=["127.0.0.1"]),
        )
    finally:
        server.shutdown()
        thread.join(timeout=2)

    assert b"Rendered by JS" in page.html


def _cloud_stdout(html: str) -> str:
    payload = {"html": html, "diagnostics": {"loaded": True}}
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return f"noise before payload\n{rendering._CLOUD_RENDER_SENTINEL}{encoded}\n"
