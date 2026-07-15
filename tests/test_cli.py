"""CLI regression tests."""

import json
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace

import pytest

import docpull
from docpull.cli import create_parser, main, run_fetcher
from docpull.models.events import EventType, SkipReason


def _capture_fetcher_config(
    monkeypatch,
    *,
    fetch_one_result=None,
    run_events=None,
    discover_urls=None,
    stats=None,
):
    captured = {}

    class FakeFetcher:
        def __init__(self, config):
            captured["config"] = config
            self.config = config
            self.stats = stats or SimpleNamespace(
                urls_discovered=2,
                pages_fetched=1,
                pages_skipped=1,
                pages_failed=0,
                duration_seconds=0.1,
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return None

        async def fetch_one(self, url):
            if fetch_one_result is not None:
                return fetch_one_result
            return SimpleNamespace(
                error=None,
                should_skip=False,
                skip_reason=None,
                skip_code=None,
                chunks=[],
                output_path=self.config.output.directory / "index.md",
                source_type="generic",
            )

        async def discover(self):
            return discover_urls or ["https://example.com/a", "https://example.com/b"]

        async def run(self):
            for event in run_events or []:
                yield event

    monkeypatch.setattr("docpull.cli.Fetcher", FakeFetcher)
    return captured


def test_runtime_version_matches_package_metadata():
    assert docpull.__version__ == version("docpull")


def test_parser_rejects_removed_js_flag():
    """Ensure the removed JavaScript flag stays unavailable."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["https://example.com", "--js"])


@pytest.mark.parametrize("alias", ["flat", "short"])
def test_parser_rejects_removed_naming_aliases(alias: str):
    """Ensure removed naming aliases stay unavailable at the CLI boundary."""
    parser = create_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["https://example.com", "--naming-strategy", alias])


def test_parser_accepts_supported_naming_strategies():
    parser = create_parser()

    full = parser.parse_args(["https://example.com", "--naming-strategy", "full"])
    hierarchical = parser.parse_args(["https://example.com", "--naming-strategy", "hierarchical"])

    assert full.naming_strategy == "full"
    assert hierarchical.naming_strategy == "hierarchical"


def test_parser_accepts_okf_profile_and_format():
    parser = create_parser()

    profile = parser.parse_args(["https://example.com", "--profile", "okf"])
    output_format = parser.parse_args(["https://example.com", "--format", "okf"])

    assert profile.profile == "okf"
    assert output_format.format == "okf"


def test_parser_accepts_budget_and_explain_route():
    parser = create_parser()

    args = parser.parse_args(["https://example.com", "--budget", "0", "--explain-route"])

    assert args.budget == 0
    assert args.explain_route is True


def test_parser_accepts_ensemble_extractor():
    parser = create_parser()

    args = parser.parse_args(["https://example.com", "--extractor", "ensemble"])

    assert args.extractor == "ensemble"


def test_render_defaults_off(monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(["https://example.com", "--single", "--quiet"])

    assert run_fetcher(args) == 0
    assert captured["config"].render.mode == "off"


def test_render_options_populate_config(monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com/app",
            "--single",
            "--render",
            "agent-browser",
            "--render-timeout",
            "5",
            "--render-wait-for",
            "networkidle",
            "--render-allowed-domain",
            "example.com",
            "--render-viewport",
            "1024x768",
            "--render-max-html-bytes",
            "2mb",
            "--quiet",
        ]
    )

    assert run_fetcher(args) == 0
    render = captured["config"].render
    assert render.mode == "agent-browser"
    assert render.backend == "agent-browser"
    assert render.timeout_seconds == 5
    assert render.wait_for == "networkidle"
    assert render.allowed_domains == ["example.com"]
    assert render.viewport.width == 1024
    assert render.viewport.height == 768
    assert int(render.max_html_bytes) == 2 * 1024 * 1024


def test_render_options_accept_cloud_runtime(tmp_path, monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com/app",
            "--single",
            "--render",
            "fallback",
            "--render-runtime",
            "e2b",
            "-o",
            str(tmp_path / "out"),
            "--quiet",
        ]
    )

    assert run_fetcher(args) == 0
    render = captured["config"].render
    assert render.mode == "fallback"
    assert render.backend == "e2b-sandbox"


def test_fetcher_budget_zero_blocks_cloud_render_before_fetcher(tmp_path, monkeypatch, capsys):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com/app",
            "--single",
            "--render",
            "fallback",
            "--render-runtime",
            "e2b",
            "--budget",
            "0",
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )

    assert run_fetcher(args) == 1
    assert captured == {}
    assert "Budget error" in capsys.readouterr().out
    accounting = tmp_path / "out" / "run.accounting.json"
    payload = json.loads(accounting.read_text(encoding="utf-8"))
    assert payload["blocked_actions"][0]["provider"] == "e2b-sandbox"


def test_run_fetcher_populates_dense_config_options(tmp_path, monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com/docs",
            "--single",
            "--quiet",
            "--profile",
            "llm",
            "--output-dir",
            str(tmp_path / "out"),
            "--format",
            "json",
            "--naming-strategy",
            "hierarchical",
            "--max-tokens-per-file",
            "5000",
            "--tokenizer",
            "cl100k_base",
            "--emit-chunks",
            "--max-pages",
            "3",
            "--max-depth",
            "2",
            "--max-concurrent",
            "4",
            "--per-host-concurrent",
            "2",
            "--rate-limit",
            "0.5",
            "--adaptive-rate-limit",
            "--no-streaming-discovery",
            "--include-path",
            "/docs/*",
            "--exclude-path",
            "/admin/*",
            "--streaming-dedup",
            "--extractor",
            "trafilatura",
            "--no-special-cases",
            "--strict-js-required",
            "--remote-documents",
            "pdf",
            "--remote-document-backend",
            "markitdown",
            "--proxy",
            "http://proxy.example:8080",
            "--user-agent",
            "docpull-test",
            "--max-retries",
            "2",
            "--require-pinned-dns",
            "--auth-header",
            "X-Test",
            "secret",
            "--auth-policy",
            "public-token-only",
            "--cache",
            "--cache-dir",
            str(tmp_path / "cache"),
            "--cache-ttl",
            "2",
            "--no-skip-unchanged",
            "--resume",
        ]
    )

    assert run_fetcher(args) == 0
    config = captured["config"]
    assert config.profile.value == "llm"
    assert config.output.format == "json"
    assert config.output.naming_strategy == "hierarchical"
    assert config.output.max_tokens_per_file == 5000
    assert config.output.tokenizer == "cl100k_base"
    assert config.output.emit_chunks is True
    assert config.crawl.max_pages == 3
    assert config.crawl.max_depth == 2
    assert config.crawl.max_concurrent == 4
    assert config.crawl.per_host_concurrent == 2
    assert config.crawl.rate_limit == 0.5
    assert config.crawl.adaptive_rate_limit is True
    assert config.crawl.streaming_discovery is False
    assert config.crawl.include_paths == ["/docs/*"]
    assert config.crawl.exclude_paths == ["/admin/*"]
    assert config.content_filter.streaming_dedup is True
    assert config.content_filter.extractor == "trafilatura"
    assert config.content_filter.remote_documents == "pdf"
    assert config.content_filter.remote_document_backend == "markitdown"
    assert config.content_filter.enable_special_cases is False
    assert config.content_filter.strict_js_required is True
    assert config.network.proxy == "http://proxy.example:8080"
    assert config.network.user_agent == "docpull-test"
    assert config.network.max_retries == 2
    assert config.network.require_pinned_dns is True
    assert config.auth.type == "header"
    assert config.auth.policy == "public-token-only"
    assert config.cache.enabled is True
    assert config.cache.directory == tmp_path / "cache"
    assert config.cache.ttl_days == 2
    assert config.cache.skip_unchanged is False
    assert config.cache.resume is True


def test_preview_urls_uses_discovery_only(monkeypatch, capsys):
    _capture_fetcher_config(monkeypatch, discover_urls=["https://example.com/one"])
    parser = create_parser()
    args = parser.parse_args(["https://example.com", "--preview-urls"])

    assert run_fetcher(args) == 0
    assert "https://example.com/one" in capsys.readouterr().out


def test_quiet_crawl_counts_skips_without_progress(monkeypatch):
    _capture_fetcher_config(
        monkeypatch,
        run_events=[
            SimpleNamespace(type=EventType.FETCH_SKIPPED, skip_reason=SkipReason.ROBOTS_DISALLOWED),
            SimpleNamespace(type=EventType.COMPLETED, message="Done"),
        ],
    )
    parser = create_parser()
    args = parser.parse_args(["https://example.com", "--quiet"])

    assert run_fetcher(args) == 0


def test_crawl_returns_nonzero_when_no_records_are_written(tmp_path, monkeypatch):
    _capture_fetcher_config(
        monkeypatch,
        run_events=[
            SimpleNamespace(type=EventType.FETCH_SKIPPED, skip_reason=SkipReason.ROBOTS_DISALLOWED),
            SimpleNamespace(type=EventType.COMPLETED, message="Done"),
        ],
        stats=SimpleNamespace(
            urls_discovered=1,
            pages_fetched=0,
            pages_skipped=1,
            pages_failed=0,
            duration_seconds=0.1,
        ),
    )
    parser = create_parser()
    args = parser.parse_args(["https://example.com", "--quiet", "-o", str(tmp_path)])

    assert run_fetcher(args) == 1


def test_non_quiet_crawl_renders_progress_and_summary(monkeypatch, capsys):
    _capture_fetcher_config(
        monkeypatch,
        run_events=[
            SimpleNamespace(type=EventType.STARTED, message="Starting"),
            SimpleNamespace(type=EventType.RESUMED, total=2),
            SimpleNamespace(type=EventType.DISCOVERY_STARTED),
            SimpleNamespace(type=EventType.DISCOVERY_COMPLETE, total=2),
            SimpleNamespace(
                type=EventType.FETCH_PROGRESS,
                processed_count=1,
                current=1,
                total=2,
                saved_count=1,
                skipped_count=0,
                failed_count=0,
                url="https://example.com/a",
            ),
            SimpleNamespace(
                type=EventType.FETCH_SKIPPED,
                skip_reason=SkipReason.HTTP_ERROR,
                url="https://example.com/b",
            ),
            SimpleNamespace(type=EventType.FETCH_FAILED, url="https://example.com/c", error="boom"),
            SimpleNamespace(type=EventType.COMPLETED, message="Done"),
        ],
    )
    parser = create_parser()
    args = parser.parse_args(["https://example.com", "--verbose"])

    assert run_fetcher(args) == 0
    output = capsys.readouterr().out
    assert "Results:" in output
    assert "http_error" in output
    assert "Failed:" in output


def test_single_fetch_reports_error_and_success(monkeypatch, capsys):
    parser = create_parser()
    _capture_fetcher_config(
        monkeypatch,
        fetch_one_result=SimpleNamespace(error="network failed", should_skip=False),
    )
    error_args = parser.parse_args(["https://example.com", "--single"])

    assert run_fetcher(error_args) == 1
    assert "network failed" in capsys.readouterr().out

    _capture_fetcher_config(
        monkeypatch,
        fetch_one_result=SimpleNamespace(
            error=None,
            should_skip=False,
            chunks=["a", "b"],
            output_path=Path("out/index.md"),
            source_type="generic",
        ),
    )
    success_args = parser.parse_args(["https://example.com", "--single"])

    assert run_fetcher(success_args) == 0
    assert "2 chunks" in capsys.readouterr().out


def test_render_subcommand_dispatches_to_helper(tmp_path, monkeypatch):
    captured = {}

    async def fake_render_url_to_directory(url, output_dir, *, config, renderer=None):
        captured["url"] = url
        captured["output_dir"] = output_dir
        captured["config"] = config
        captured["renderer"] = renderer
        return SimpleNamespace(
            html_path=output_dir / "index.html",
            sidecar_path=output_dir / "rendered_pages.ndjson",
        )

    monkeypatch.setattr("docpull.cli.render_url_to_directory", fake_render_url_to_directory)

    result = main(
        [
            "render",
            "https://example.com/app",
            "--output-dir",
            str(tmp_path),
            "--timeout",
            "9",
            "--runtime",
            "e2b",
            "--cloud-agent-browser-install",
            "skip",
            "--cloud-result-transport",
            "file",
            "--cloud-max-estimated-cost",
            "0.25",
            "--template",
            "docpull-agent-browser",
            "--agent-browser-bin",
            "/usr/local/bin/agent-browser",
            "--quiet",
        ]
    )

    assert result == 0
    assert captured["url"] == "https://example.com/app"
    assert captured["output_dir"] == tmp_path
    assert captured["config"].mode == "agent-browser"
    assert captured["config"].backend == "e2b-sandbox"
    assert captured["config"].timeout_seconds == 9
    assert captured["config"].cloud_agent_browser_install == "skip"
    assert captured["config"].cloud_result_transport == "file"
    assert captured["config"].cloud_max_estimated_cost_usd == 0.25
    assert captured["config"].e2b_template == "docpull-agent-browser"
    assert captured["config"].cloud_agent_browser_binary == "/usr/local/bin/agent-browser"
    assert captured["renderer"] is None


def test_render_live_smoke_uses_default_url_and_temp_output(monkeypatch, capsys):
    captured = {}

    async def fake_render_url_to_directory(url, output_dir, *, config, renderer=None):
        captured["url"] = url
        captured["output_dir"] = output_dir
        return SimpleNamespace(
            html_path=output_dir / "index.html",
            sidecar_path=output_dir / "rendered_pages.ndjson",
        )

    monkeypatch.setattr("docpull.cli.render_url_to_directory", fake_render_url_to_directory)

    assert main(["render", "--live-smoke", "--quiet"]) == 0

    assert captured["url"] == "https://example.com"
    assert "docpull-render-smoke-" in str(captured["output_dir"])
    assert capsys.readouterr().out == ""


def test_render_live_smoke_honors_explicit_output_dir(tmp_path, monkeypatch, capsys):
    captured = {}

    async def fake_render_url_to_directory(url, output_dir, *, config, renderer=None):
        captured["url"] = url
        captured["output_dir"] = output_dir
        return SimpleNamespace(
            html_path=output_dir / "index.html",
            sidecar_path=output_dir / "rendered_pages.ndjson",
        )

    monkeypatch.setattr("docpull.cli.render_url_to_directory", fake_render_url_to_directory)

    assert main(["render", "https://example.com/app", "--live-smoke", "-o", str(tmp_path), "--quiet"]) == 0

    assert captured["url"] == "https://example.com/app"
    assert captured["output_dir"] == tmp_path
    assert capsys.readouterr().out == ""


def test_render_init_prints_agent_browser_template_recipe(capsys):
    assert main(["render", "init", "e2b", "--template", "docpull-agent-browser"]) == 0

    output = capsys.readouterr().out
    assert "npm install -g agent-browser" in output
    assert "--runtime e2b" in output


def test_render_doctor_reports_all_runtimes(monkeypatch, capsys):
    monkeypatch.setattr(
        "docpull.cli.check_render_backend_availability",
        lambda backend, binary=None: (backend == "agent-browser", f"{backend}:checked"),
    )

    assert main(["render", "doctor"]) == 0

    output = capsys.readouterr().out
    assert "local:" in output
    assert "vercel:" in output
    assert "e2b:" in output


def test_render_check_reports_available(monkeypatch, capsys):
    seen = {}

    def fake_check(backend, binary=None):
        seen["backend"] = backend
        seen["binary"] = binary
        return True, f"[OK] {backend} backend"

    monkeypatch.setattr(
        "docpull.cli.check_render_backend_availability",
        fake_check,
    )

    assert main(["render", "--check", "--runtime", "vercel", "--vercel-sandbox-bin", "/bin/sandbox"]) == 0

    assert seen == {"backend": "vercel-sandbox", "binary": "/bin/sandbox"}
    assert "vercel-sandbox backend" in capsys.readouterr().out


def test_render_check_reports_missing(monkeypatch, capsys):
    monkeypatch.setattr(
        "docpull.cli.check_render_backend_availability",
        lambda _backend, binary=None: (False, "[WARN] backend unavailable"),
    )

    assert main(["render", "--check", "--runtime", "e2b"]) == 1

    assert "unavailable" in capsys.readouterr().out


def test_parser_accepts_sec_filing_profile():
    parser = create_parser()

    args = parser.parse_args(["https://www.sec.gov/Archives/example.htm", "--profile", "sec-filing"])

    assert args.profile == "sec-filing"


def test_skill_rejects_okf_output(tmp_path, capsys):
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--skill",
            "my-docs",
            "--format",
            "okf",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert run_fetcher(args) == 1
    captured = capsys.readouterr()
    assert "--skill cannot be combined with OKF output" in captured.out


def test_skill_rejects_non_markdown_output(tmp_path, capsys):
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--skill",
            "my-docs",
            "--format",
            "ndjson",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert run_fetcher(args) == 1
    captured = capsys.readouterr()
    assert "--skill requires markdown output" in captured.out


def test_skill_agent_all_uses_references_layout(tmp_path, monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--single",
            "--skill",
            "my-docs",
            "--skill-agent",
            "all",
            "--output-dir",
            str(tmp_path),
            "--quiet",
        ]
    )

    assert run_fetcher(args) == 0
    config = captured["config"]
    assert config.output.skill_agents == ["claude", "codex", "cursor"]
    assert config.output.skill_root_dir == tmp_path / "my-docs"
    assert config.output.directory == tmp_path / "my-docs" / "references"
    assert config.output.skill_install_targets is True


def test_skill_agent_all_defaults_to_shared_corpus_root(monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--single",
            "--skill",
            "my-docs",
            "--skill-agent",
            "all",
            "--quiet",
        ]
    )

    assert run_fetcher(args) == 0
    config = captured["config"]
    assert config.output.skill_agents == ["claude", "codex", "cursor"]
    assert config.output.skill_root_dir == Path(".docpull/skills/my-docs")
    assert config.output.directory == Path(".docpull/skills/my-docs/references")
    assert config.output.skill_install_targets is True


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_explicit_single_skill_agent_defaults_to_shared_corpus_root(agent: str, monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--single",
            "--skill",
            "my-docs",
            "--skill-agent",
            agent,
            "--quiet",
        ]
    )

    assert run_fetcher(args) == 0
    config = captured["config"]
    assert config.output.skill_agents == [agent]
    assert config.output.skill_root_dir == Path(".docpull/skills/my-docs")
    assert config.output.directory == Path(".docpull/skills/my-docs/references")
    assert config.output.skill_install_targets is True


def test_skill_agent_codex_cursor_uses_shared_corpus_root(monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--single",
            "--skill",
            "my-docs",
            "--skill-agent",
            "codex",
            "--skill-agent",
            "cursor",
            "--quiet",
        ]
    )

    assert run_fetcher(args) == 0
    config = captured["config"]
    assert config.output.skill_agents == ["codex", "cursor"]
    assert config.output.skill_root_dir == Path(".docpull/skills/my-docs")
    assert config.output.directory == Path(".docpull/skills/my-docs/references")
    assert config.output.skill_install_targets is True


def test_skill_agent_cursor_uses_shared_corpus_root(monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--single",
            "--skill",
            "my-docs",
            "--skill-agent",
            "cursor",
            "--quiet",
        ]
    )

    assert run_fetcher(args) == 0
    config = captured["config"]
    assert config.output.skill_agents == ["cursor"]
    assert config.output.skill_root_dir == Path(".docpull/skills/my-docs")
    assert config.output.directory == Path(".docpull/skills/my-docs/references")
    assert config.output.skill_install_targets is True


def test_skill_without_explicit_agent_preserves_legacy_claude_root(monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--single",
            "--skill",
            "my-docs",
            "--quiet",
        ]
    )

    assert run_fetcher(args) == 0
    config = captured["config"]
    assert config.output.skill_agents == ["claude"]
    assert config.output.skill_root_dir == Path(".claude/skills/my-docs")
    assert config.output.directory == Path(".claude/skills/my-docs/references")
    assert config.output.skill_install_targets is False


def test_skill_output_dir_without_explicit_agent_preserves_staging_only(tmp_path, monkeypatch):
    captured = _capture_fetcher_config(monkeypatch)
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--single",
            "--skill",
            "my-docs",
            "--output-dir",
            str(tmp_path),
            "--quiet",
        ]
    )

    assert run_fetcher(args) == 0
    config = captured["config"]
    assert config.output.skill_agents == ["claude"]
    assert config.output.skill_root_dir == tmp_path / "my-docs"
    assert config.output.directory == tmp_path / "my-docs" / "references"
    assert config.output.skill_install_targets is False


def test_parser_accepts_per_host_concurrency():
    parser = create_parser()

    args = parser.parse_args(["https://example.com", "--max-concurrent", "50", "--per-host-concurrent", "10"])

    assert args.max_concurrent == 50
    assert args.per_host_concurrent == 10


def test_help_describes_insecure_tls_as_rejected():
    parser = create_parser()

    assert "Deprecated and rejected" in parser.format_help()


def test_help_describes_mirror_naming_override():
    parser = create_parser()
    help_text = " ".join(parser.format_help().split())

    assert "Mirror profile defaults to hierarchical unless explicitly overridden" in help_text


def test_single_invalid_url_returns_nonzero(tmp_path):
    parser = create_parser()
    args = parser.parse_args(["http://example.com", "--single", "--output-dir", str(tmp_path)])

    assert run_fetcher(args) == 1


def test_configuration_errors_escape_rich_markup(tmp_path, capsys):
    parser = create_parser()
    args = parser.parse_args(
        [
            "https://example.com",
            "--single",
            "--skill",
            "BadName",
            "--output-dir",
            str(tmp_path),
        ]
    )

    assert run_fetcher(args) == 1
    captured = capsys.readouterr()
    assert r"^[a-z0-9][a-z0-9-]*$" in captured.out


def test_single_no_content_skip_returns_nonzero(tmp_path, monkeypatch):
    _capture_fetcher_config(
        monkeypatch,
        fetch_one_result=SimpleNamespace(
            error=None,
            should_skip=True,
            skip_reason="No content extracted",
            skip_code=SkipReason.NO_CONTENT_EXTRACTED,
        ),
    )
    parser = create_parser()
    args = parser.parse_args(["https://example.com/empty", "--single", "--output-dir", str(tmp_path)])

    assert run_fetcher(args) == 1
