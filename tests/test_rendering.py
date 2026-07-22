"""Tests for optional local rendering."""

from __future__ import annotations

import base64
import json
import os
import shlex
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
    build_degradation_report,
    check_agent_browser_availability,
    check_e2b_sandbox_availability,
    check_render_backend_availability,
    check_vercel_sandbox_availability,
    estimate_cloud_render_cost_usd,
    render_url_to_directory,
)


def _trust_browser_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS", "1")


def test_agent_browser_binary_prefers_explicit_value_and_env(monkeypatch):
    monkeypatch.setenv("DOCPULL_AGENT_BROWSER_BIN", "/opt/bin/custom-agent-browser")

    assert agent_browser_binary() == "/opt/bin/custom-agent-browser"
    assert agent_browser_binary("/tmp/agent-browser") == "/tmp/agent-browser"


def test_check_agent_browser_availability_reports_path(monkeypatch):
    monkeypatch.delenv("DOCPULL_AGENT_BROWSER_BIN", raising=False)
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
        "--session",
        "docpull-render-08d099a4ffed",
        "batch",
        "--bail",
        "--json",
        "set viewport 800 600",
        "open https://example.com/app",
        "wait --load networkidle",
        "get html html",
        "close",
    ]


def test_agent_browser_command_appends_screenshot_step_before_close(tmp_path):
    config = RenderConfig(mode="agent-browser", viewport="800x600")
    shot_path = tmp_path / "shot.png"

    command = build_agent_browser_command(
        "https://example.com/app",
        config,
        binary="/opt/bin/agent-browser",
        screenshot_path=shot_path,
    )

    # The screenshot step must not change the deterministic session name.
    assert command[2] == "docpull-render-08d099a4ffed"
    assert command[-3:] == [
        "get html html",
        f"screenshot {shlex.quote(str(shot_path))}",
        "close",
    ]


def test_agent_browser_command_cookie_header_step_and_distinct_session():
    anonymous = build_agent_browser_command(
        "https://example.com/app",
        RenderConfig(mode="agent-browser"),
    )
    config = RenderConfig(mode="agent-browser", cookie_env="DOCS_COOKIE")

    command = build_agent_browser_command(
        "https://example.com/app",
        config,
        cookie_header="session=super-secret",
    )

    header_steps = [part for part in command if part.startswith("set headers ")]
    assert len(header_steps) == 1
    assert json.loads(shlex.split(header_steps[0])[2]) == {"Cookie": "session=super-secret"}
    # Header must be staged before the first navigation.
    assert command.index(header_steps[0]) < command.index("open https://example.com/app")
    # Authenticated sessions must not share browser state with anonymous ones,
    # but the name stays deterministic and derived from the env var NAME only.
    assert command[2] != anonymous[2]
    assert command[2].startswith("docpull-render-")
    assert command[2] == build_agent_browser_command("https://example.com/app", config)[2]


def test_build_degradation_report_detects_wait_timeout_and_healthy_none():
    degraded = build_degradation_report(
        payload=[
            {"command": "open https://example.com/app", "success": True},
            {"command": "wait --load networkidle", "error": "Timeout 30000ms exceeded"},
            {"command": "get html html", "data": "<html></html>"},
        ]
    )

    assert degraded == {
        "html_truncated": False,
        "wait_timeout": True,
        "screenshot_failed": False,
        "notes": ["wait step reported a timeout: Timeout 30000ms exceeded"],
    }
    assert build_degradation_report(payload=[{"command": "open x", "success": True}]) is None


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
    assert (
        '"agent-browser", "--session", "docpull-render-08d099a4ffed", "batch", "--bail", "--json"'
    ) in script


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
async def test_agent_browser_missing_binary_error_is_actionable(monkeypatch):
    _trust_browser_targets(monkeypatch)

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
async def test_render_requires_trusted_browser_target_override():
    async def runner(command, timeout):
        raise AssertionError("renderer should not be called")

    renderer = AgentBrowserRenderer(runner=runner)

    with pytest.raises(RenderError, match="Browser rendering is disabled"):
        await renderer.render("https://example.com/app", RenderConfig(mode="agent-browser"))


@pytest.mark.asyncio
async def test_render_blocks_private_and_local_targets():
    async def runner(command, timeout):
        raise AssertionError("renderer should not be called")

    renderer = AgentBrowserRenderer(runner=runner)

    for url in ("https://127.0.0.1/admin", "https://localhost/admin"):
        with pytest.raises(RenderError, match="Render URL validation failed"):
            await renderer.render(url, RenderConfig(mode="agent-browser"))


@pytest.mark.asyncio
async def test_render_requires_explicit_local_target_override(monkeypatch):
    calls = []

    async def runner(command, timeout):
        calls.append(command)
        return CommandResult(
            returncode=0,
            stdout=json.dumps({"html": "<html><body>local</body></html>"}),
            stderr="",
        )

    renderer = AgentBrowserRenderer(runner=runner)

    with pytest.raises(RenderError, match="Render URL validation failed"):
        await renderer.render(
            "http://127.0.0.1:8080/",
            RenderConfig(mode="agent-browser", allowed_domains=["127.0.0.1"]),
        )

    monkeypatch.setenv("DOCPULL_RENDER_ALLOW_LOCAL_TARGETS", "1")
    page = await renderer.render(
        "http://127.0.0.1:8080/",
        RenderConfig(mode="agent-browser", allowed_domains=["127.0.0.1"]),
    )

    assert calls
    assert b"local" in page.html


@pytest.mark.asyncio
async def test_render_blocks_non_restrictive_action_policy(monkeypatch):
    _trust_browser_targets(monkeypatch)

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
async def test_render_url_to_directory_writes_html_and_sidecar(tmp_path, monkeypatch):
    _trust_browser_targets(monkeypatch)

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

    assert artifact.html_path.name.startswith("example.com_app_")
    assert artifact.html_path.name.endswith(".html")
    assert artifact.html_path.read_text(encoding="utf-8") == "<html><body><h1>Rendered</h1></body></html>"
    records = [json.loads(line) for line in artifact.sidecar_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["schema_version"] == 1
    assert record["url"] == "https://example.com/app"
    assert record["source"] == "docpull_render_cli"
    assert record["backend"] == "agent-browser"
    assert record["artifact_path"] == artifact.html_path.name
    assert record["diagnostics"]["loaded"] is True
    assert record["allowed_domains"] == ["example.com"]


@pytest.mark.asyncio
async def test_render_url_to_directory_keeps_distinct_urls_from_overwriting(tmp_path, monkeypatch):
    _trust_browser_targets(monkeypatch)

    counter = 0

    async def runner(command, timeout):
        nonlocal counter
        counter += 1
        return CommandResult(
            returncode=0,
            stdout=json.dumps({"html": f"<html><body>render {counter}</body></html>"}),
            stderr="",
        )

    renderer = AgentBrowserRenderer(runner=runner)

    first = await render_url_to_directory(
        "https://93.184.216.34/",
        tmp_path,
        config=RenderConfig(mode="agent-browser"),
        renderer=renderer,
    )
    second = await render_url_to_directory(
        "https://1.1.1.1/",
        tmp_path,
        config=RenderConfig(mode="agent-browser"),
        renderer=renderer,
    )

    assert first.html_path != second.html_path
    assert first.html_path.read_text(encoding="utf-8") == "<html><body>render 1</body></html>"
    assert second.html_path.read_text(encoding="utf-8") == "<html><body>render 2</body></html>"


@pytest.mark.asyncio
async def test_render_screenshot_artifact_lands_next_to_html(tmp_path, monkeypatch):
    _trust_browser_targets(monkeypatch)

    async def runner(command, timeout):
        for part in command:
            if part.startswith("screenshot "):
                with open(shlex.split(part)[1], "wb") as fh:
                    fh.write(b"fake-png-bytes")
        return CommandResult(
            returncode=0,
            stdout=json.dumps({"html": "<html><body>shot</body></html>"}),
            stderr="",
        )

    renderer = AgentBrowserRenderer(runner=runner)

    artifact = await render_url_to_directory(
        "https://example.com/app",
        tmp_path,
        config=RenderConfig(mode="agent-browser", screenshot=True),
        renderer=renderer,
    )

    page = artifact.page
    assert page.screenshot_path is not None
    assert page.screenshot_path == artifact.html_path.with_name(f"{artifact.html_path.stem}.render.png")
    assert page.screenshot_path.read_bytes() == b"fake-png-bytes"
    assert page.diagnostics["screenshot_path"] == str(page.screenshot_path)
    assert "degradation" not in page.diagnostics
    record = json.loads(artifact.sidecar_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["screenshot_path"] == page.screenshot_path.name


@pytest.mark.asyncio
async def test_render_screenshot_missing_file_records_degradation(monkeypatch):
    _trust_browser_targets(monkeypatch)

    async def runner(command, timeout):
        return CommandResult(
            returncode=0,
            stdout=json.dumps({"html": "<html><body>no shot</body></html>"}),
            stderr="",
        )

    renderer = AgentBrowserRenderer(runner=runner)

    page = await renderer.render(
        "https://example.com/app",
        RenderConfig(mode="agent-browser", screenshot=True),
    )

    assert page.screenshot_path is None
    degradation = page.diagnostics["degradation"]
    assert degradation["screenshot_failed"] is True
    assert degradation["html_truncated"] is False
    assert degradation["wait_timeout"] is False
    assert degradation["notes"]


@pytest.mark.asyncio
async def test_render_cookie_env_injects_header_and_masks_value(monkeypatch):
    _trust_browser_targets(monkeypatch)
    monkeypatch.setenv("DOCS_COOKIE", "session=secret-cookie-value")

    captured: list[str] = []

    async def runner(command, timeout):
        captured.extend(command)
        return CommandResult(
            returncode=0,
            stdout=json.dumps({"html": "<html><body>authed</body></html>"}),
            stderr="",
        )

    renderer = AgentBrowserRenderer(runner=runner)

    page = await renderer.render(
        "https://example.com/app",
        RenderConfig(mode="agent-browser", cookie_env="DOCS_COOKIE"),
    )

    # The real value reaches the agent-browser invocation...
    assert any("secret-cookie-value" in part for part in captured)
    # ...but never any diagnostics, and the session differs from the anonymous pin.
    assert "secret-cookie-value" not in json.dumps(page.diagnostics)
    assert "set headers cookie_env:DOCS_COOKIE" in page.diagnostics["command"]
    assert page.diagnostics["cookie"] == "cookie_env:DOCS_COOKIE"
    assert captured[2] != "docpull-render-08d099a4ffed"


@pytest.mark.asyncio
async def test_render_cookie_env_missing_is_clear_error(monkeypatch):
    _trust_browser_targets(monkeypatch)
    monkeypatch.delenv("DOCS_COOKIE", raising=False)

    async def runner(command, timeout):
        raise AssertionError("renderer should not be called")

    renderer = AgentBrowserRenderer(runner=runner)

    with pytest.raises(RenderError, match="DOCS_COOKIE"):
        await renderer.render(
            "https://example.com/app",
            RenderConfig(mode="agent-browser", cookie_env="DOCS_COOKIE"),
        )


@pytest.mark.asyncio
async def test_render_truncates_html_at_max_bytes_and_records_degradation(monkeypatch):
    _trust_browser_targets(monkeypatch)

    html = "<html><body>" + "x" * 100 + "</body></html>"

    async def runner(command, timeout):
        return CommandResult(returncode=0, stdout=json.dumps({"html": html}), stderr="")

    renderer = AgentBrowserRenderer(runner=runner)

    page = await renderer.render(
        "https://example.com/app",
        RenderConfig(mode="agent-browser", max_html_bytes=16),
    )

    assert page.html == html.encode("utf-8")[:16]
    degradation = page.diagnostics["degradation"]
    assert degradation["html_truncated"] is True
    assert degradation["wait_timeout"] is False
    assert degradation["screenshot_failed"] is False
    assert any("max_html_bytes" in note for note in degradation["notes"])


@pytest.mark.asyncio
async def test_cloud_renderers_refuse_session_cookies(monkeypatch):
    _trust_browser_targets(monkeypatch)

    async def runner(command, timeout):
        raise AssertionError("renderer should not be called")

    def sandbox_factory():
        raise AssertionError("sandbox should not be created")

    vercel = VercelSandboxRenderer(binary="/opt/bin/sandbox", runner=runner)
    e2b = E2BSandboxRenderer(sandbox_factory=sandbox_factory)
    for renderer, backend in ((vercel, "vercel-sandbox"), (e2b, "e2b-sandbox")):
        with pytest.raises(RenderError, match="local agent-browser runtime"):
            await renderer.render(
                "https://example.com/app",
                RenderConfig(mode="agent-browser", backend=backend, cookie_env="DOCS_COOKIE"),
            )


@pytest.mark.asyncio
async def test_cloud_screenshot_reports_unsupported_transport(monkeypatch):
    _trust_browser_targets(monkeypatch)

    async def runner(command, timeout):
        return CommandResult(
            returncode=0,
            stdout=_cloud_stdout("<html><body><h1>Cloud</h1></body></html>"),
            stderr="",
        )

    renderer = VercelSandboxRenderer(binary="/opt/bin/sandbox", runner=runner)

    page = await renderer.render(
        "https://example.com/app",
        RenderConfig(mode="agent-browser", backend="vercel-sandbox", screenshot=True),
    )

    assert page.screenshot_path is None
    assert page.diagnostics["screenshot"] == "unsupported_transport"


@pytest.mark.asyncio
async def test_vercel_sandbox_renderer_uses_cli_and_parses_payload(monkeypatch):
    _trust_browser_targets(monkeypatch)

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
async def test_cloud_render_budget_cap_blocks_before_provider_call(monkeypatch):
    _trust_browser_targets(monkeypatch)

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
async def test_e2b_sandbox_renderer_runs_command_and_kills_sandbox(monkeypatch):
    _trust_browser_targets(monkeypatch)

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
async def test_e2b_sandbox_renderer_prefers_file_transport(monkeypatch):
    _trust_browser_targets(monkeypatch)

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
    if os.environ.get("DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS") != "1":
        pytest.skip("Set DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 for trusted live render targets.")
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
async def test_live_agent_browser_backend_executes_js_when_available(monkeypatch):
    available, message = check_agent_browser_availability()
    if not available:
        pytest.skip(message)
    monkeypatch.setenv("DOCPULL_RENDER_ALLOW_LOCAL_TARGETS", "1")

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
