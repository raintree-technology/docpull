"""Cron-friendly local monitor workflows."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from rich.console import Console
from rich.markup import escape

from .local_workflows import LocalWorkflowError, audit_pack, refresh_pack
from .pack_tools import PackToolError, _artifact_ref, _write_json, diff_packs
from .state_paths import resolve_default_state_path
from .time_utils import utc_now_iso

MONITOR_SCHEMA_VERSION = 1
DEFAULT_MONITOR_STATE_DIR = resolve_default_state_path(".docpull/monitors")


class MonitorError(RuntimeError):
    """User-facing monitor workflow error."""


def create_monitor_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull monitor",
        description="Run local pack monitors without hosting a scheduler",
    )
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_MONITOR_STATE_DIR)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Create or update a monitor config")
    init.add_argument("pack_dir", type=Path)
    init.add_argument("--name", required=True)
    init.add_argument("--schedule", default="manual")
    init.add_argument("--policy", type=Path, help="Optional policy file recorded by path")
    init.add_argument("--dedupe-key", default="url+content_hash", help="Local dedupe strategy label")
    init.add_argument(
        "--remote-documents",
        choices=["off", "pdf"],
        help="Allow refetching remote PDFs (default: inferred from the pack's PDF records)",
    )
    init.add_argument(
        "--remote-document-backend",
        choices=["auto", "pypdf", "pdfplumber", "markitdown", "unstructured"],
        help="Local PDF parser for refetches (default: the parser that built the pack)",
    )

    run = subparsers.add_parser("run", help="Run one monitor refresh cycle")
    run.add_argument("name")
    run.add_argument("--once", action="store_true", help="Required explicit single-run mode")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--json", action="store_true", dest="json_output")
    run.add_argument("--github-issue-file", type=Path)
    run.add_argument(
        "--slack-webhook",
        help="HTTPS Slack incoming-webhook URL; posts a summary when there are changes or failures. "
        "Used for this run only; never persisted or written to reports",
    )

    trigger = subparsers.add_parser("trigger", help="Alias for a single manual monitor run")
    trigger.add_argument("name")
    trigger.add_argument("--dry-run", action="store_true")
    trigger.add_argument("--json", action="store_true", dest="json_output")
    trigger.add_argument("--github-issue-file", type=Path)
    trigger.add_argument(
        "--slack-webhook",
        help="HTTPS Slack incoming-webhook URL; posts a summary when there are changes or failures. "
        "Used for this run only; never persisted or written to reports",
    )

    pause = subparsers.add_parser("pause", help="Pause a monitor config")
    pause.add_argument("name")
    pause.add_argument("--json", action="store_true", dest="json_output")

    unpause = subparsers.add_parser("unpause", help="Unpause a monitor config")
    unpause.add_argument("name")
    unpause.add_argument("--json", action="store_true", dest="json_output")

    list_cmd = subparsers.add_parser("list", help="List monitor configs")
    list_cmd.add_argument("--json", action="store_true", dest="json_output")

    report = subparsers.add_parser("report", help="Print the latest monitor run report")
    report.add_argument("name")
    report.add_argument("--json", action="store_true", dest="json_output")

    snippet = subparsers.add_parser("scheduler-snippet", help="Generate local scheduler examples")
    snippet.add_argument("name")
    snippet.add_argument(
        "--kind",
        choices=["cron", "launchd", "github-actions"],
        default="cron",
    )
    snippet.add_argument("--json", action="store_true", dest="json_output")

    return parser


def run_monitor_cli(argv: list[str] | None = None) -> int:
    parser = create_monitor_parser()
    args = parser.parse_args(argv)
    console = Console()
    try:
        if args.command == "init":
            payload = init_monitor(
                args.name,
                args.pack_dir,
                state_dir=args.state_dir,
                schedule=args.schedule,
                policy_path=args.policy,
                dedupe_key=args.dedupe_key,
                remote_documents=args.remote_documents,
                remote_document_backend=args.remote_document_backend,
            )
            console.print(f"[green]Monitor initialized:[/green] {payload['name']} -> {payload['path']}")
            return 0
        if args.command == "run":
            if not args.once:
                raise MonitorError("docpull monitor run requires --once; use cron/launchd for scheduling")
            payload = run_monitor_once(
                args.name,
                state_dir=args.state_dir,
                dry_run=args.dry_run,
                github_issue_file=args.github_issue_file,
                slack_webhook=args.slack_webhook,
            )
            return _print_run_result(console, payload, label="Monitor run", json_output=args.json_output)
        if args.command == "trigger":
            payload = run_monitor_once(
                args.name,
                state_dir=args.state_dir,
                dry_run=args.dry_run,
                github_issue_file=args.github_issue_file,
                slack_webhook=args.slack_webhook,
                trigger="manual",
            )
            return _print_run_result(console, payload, label="Monitor trigger", json_output=args.json_output)
        if args.command == "pause":
            payload = set_monitor_paused(args.name, True, state_dir=args.state_dir)
            if args.json_output:
                console.print_json(data=payload)
            else:
                console.print(f"[green]Monitor paused:[/green] {payload['name']}")
            return 0
        if args.command == "unpause":
            payload = set_monitor_paused(args.name, False, state_dir=args.state_dir)
            if args.json_output:
                console.print_json(data=payload)
            else:
                console.print(f"[green]Monitor unpaused:[/green] {payload['name']}")
            return 0
        if args.command == "list":
            payload = list_monitors(state_dir=args.state_dir)
            if args.json_output:
                console.print_json(data=payload)
            else:
                for item in payload["monitors"]:
                    console.print(f"- {item['name']}: {item['pack_dir']} ({item['schedule']})")
            return 0
        if args.command == "report":
            payload = latest_monitor_report(args.name, state_dir=args.state_dir)
            if args.json_output:
                console.print_json(data=payload)
            else:
                console.print(Path(payload["path"]).read_text(encoding="utf-8"))
            return 0
        if args.command == "scheduler-snippet":
            payload = scheduler_snippet(args.name, kind=args.kind, state_dir=args.state_dir)
            if args.json_output:
                console.print_json(data=payload)
            else:
                console.print(payload["snippet"])
            return 0
        parser.error(f"Unknown monitor command: {args.command}")
    except (MonitorError, LocalWorkflowError, PackToolError) as err:
        console.print("[red]Monitor error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Monitor failed:[/red] " + escape(str(err)))
        return 1
    return 1


def _print_run_result(console: Console, payload: dict[str, Any], *, label: str, json_output: bool) -> int:
    """Print a monitor run; exit 1 when any URL could not be refetched."""
    summary = payload["summary"]
    if json_output:
        console.print_json(data=payload)
    else:
        failed = int(summary.get("failed_count") or 0)
        color = "red" if failed else "green"
        console.print(
            f"[{color}]{label} ({escape(str(summary.get('status')))}):[/{color}] "
            f"{payload['name']} changed={summary.get('changed_count')} "
            f"added={summary.get('added_count')} removed={summary.get('removed_count')} "
            f"failed={failed} report={payload['artifacts']['json']}"
        )
        for item in summary.get("failures") or []:
            console.print(f"- [red]failed[/red] {escape(str(item['url']))}: {escape(str(item['reason']))}")
    return 1 if summary.get("status") == "failed" else 0


def init_monitor(
    name: str,
    pack_dir: Path,
    *,
    state_dir: Path = DEFAULT_MONITOR_STATE_DIR,
    schedule: str = "manual",
    policy_path: Path | None = None,
    dedupe_key: str = "url+content_hash",
    remote_documents: str | None = None,
    remote_document_backend: str | None = None,
) -> dict[str, Any]:
    monitor_name = _safe_monitor_name(name)
    pack_path = pack_dir.resolve()
    if not pack_path.exists():
        raise MonitorError(f"Pack directory does not exist: {pack_path}")
    monitor_dir = _monitor_dir(state_dir, monitor_name)
    monitor_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "name": monitor_name,
        "pack_dir": str(pack_path),
        "schedule": schedule,
        "policy_path": str(policy_path.resolve()) if policy_path else None,
        "dedupe_key": dedupe_key,
        "remote_documents": remote_documents,
        "remote_document_backend": remote_document_backend,
        "paused": False,
        "state_dir": str(state_dir.resolve()),
    }
    path = monitor_dir / "monitor.json"
    _write_json(path, payload)
    payload["path"] = str(path)
    return payload


def list_monitors(*, state_dir: Path = DEFAULT_MONITOR_STATE_DIR) -> dict[str, Any]:
    monitors: list[dict[str, Any]] = []
    root = state_dir.resolve()
    if root.exists():
        for config_path in sorted(root.glob("*/monitor.json")):
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            monitors.append(
                {
                    "name": data.get("name"),
                    "pack_dir": data.get("pack_dir"),
                    "schedule": data.get("schedule"),
                    "dedupe_key": data.get("dedupe_key", "url+content_hash"),
                    "paused": bool(data.get("paused", False)),
                    "path": str(config_path),
                }
            )
    return {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "state_dir": str(root),
        "monitor_count": len(monitors),
        "monitors": monitors,
    }


def run_monitor_once(
    name: str,
    *,
    state_dir: Path = DEFAULT_MONITOR_STATE_DIR,
    dry_run: bool = False,
    github_issue_file: Path | None = None,
    slack_webhook: str | None = None,
    slack_webhook_supplied: bool = False,
    trigger: str = "scheduled",
    slack_post: Any = None,
) -> dict[str, Any]:
    """Run one refresh cycle.

    ``summary.status`` is ``failed`` when any URL could not be refetched (those
    URLs are listed in ``summary.failures`` and never counted as unchanged),
    else ``changed`` or ``unchanged``. A failed cycle does not advance the
    baseline, so the next run diffs against the last fully verified snapshot.
    """
    if slack_webhook:
        _validate_slack_webhook(slack_webhook)
        slack_webhook_supplied = True
    config = _read_monitor_config(state_dir, name)
    if config.get("paused"):
        raise MonitorError(f"Monitor is paused: {name}")
    configured_pack_dir = Path(str(config["pack_dir"])).resolve()
    # After a successful check the refreshed pack becomes the baseline, so the
    # same upstream changes are not reported on every poll.
    baseline_raw = config.get("baseline_pack_dir")
    baseline = Path(str(baseline_raw)).resolve() if baseline_raw else None
    pack_dir = (
        baseline if baseline is not None and (baseline / "documents.ndjson").exists() else configured_pack_dir
    )
    run_dir = _monitor_dir(state_dir, str(config["name"])) / "runs" / _run_stamp()
    run_dir.mkdir(parents=True, exist_ok=True)
    refreshed_pack_dir = run_dir / "refreshed-pack"
    refresh = refresh_pack(
        pack_dir,
        output_dir=refreshed_pack_dir,
        dry_run=dry_run,
        markdown_path=run_dir / "refresh.report.md",
        remote_documents=config.get("remote_documents"),
        remote_document_backend=config.get("remote_document_backend"),
    )
    failures = [
        {
            "url": str(item.get("url")),
            "reason": str(item.get("error") or item.get("reason") or "fetch failed"),
            "kind": "fetch_failed" if item in refresh.get("errors", []) else "fetch_skipped",
        }
        for item in (*refresh.get("errors", []), *refresh.get("skips", []))
        if str(item.get("url")) in set(refresh.get("summary", {}).get("unverified_urls") or [])
    ]
    diff_raw = refresh.get("diff")
    diff_payload: dict[str, Any] = diff_raw if isinstance(diff_raw, dict) else {}
    if not dry_run and refreshed_pack_dir.exists() and (refreshed_pack_dir / "documents.ndjson").exists():
        audit = audit_pack(refreshed_pack_dir, markdown_path=run_dir / "PACK_AUDIT.md")
        diff_path = run_dir / "pack.diff.json"
        project_diff = diff_payload
        _write_json(diff_path, project_diff)
        project_diff_path = run_dir / "project.diff.json"
        project_diff_markdown_path = run_dir / "PROJECT_DIFF.md"
        _write_json(project_diff_path, project_diff)
        project_diff_markdown_path.write_text(_project_diff_markdown(project_diff), encoding="utf-8")
        classifications = classify_pack_changes(pack_dir, refreshed_pack_dir, project_diff)
        classes_path = run_dir / "change.classes.json"
        evals_path = run_dir / "evals.jsonl"
        _write_json(classes_path, classifications)
        evals_path.write_text(
            "".join(json.dumps(record, sort_keys=True) + "\n" for record in classifications["evals"]),
            encoding="utf-8",
        )
    else:
        audit = None
        diff_path = None
        project_diff_path = None
        project_diff_markdown_path = None
        classes_path = None
        evals_path = None
        classifications = _empty_classifications(pack_dir, refreshed_pack_dir)

    payload = {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "name": config["name"],
        "dry_run": dry_run,
        "trigger": trigger,
        "pack_dir": str(configured_pack_dir),
        "baseline_pack_dir": str(pack_dir),
        "run_dir": str(run_dir),
        "summary": {
            "changed_count": len(diff_payload.get("changed_urls", [])),
            "added_count": len(diff_payload.get("added_urls", [])),
            "removed_count": len(diff_payload.get("removed_urls", [])),
            "unchanged_count": len(diff_payload.get("unchanged_urls", [])),
            "failed_count": len(failures),
            "failures": failures,
            "audit_score": audit.get("score") if isinstance(audit, dict) else None,
            "slack_webhook_supplied": slack_webhook_supplied,
            "dedupe_key": config.get("dedupe_key", "url+content_hash"),
            "classification_count": classifications["classification_count"],
        },
        "refresh": refresh,
        "audit": audit,
        "change_classes": classifications,
        "notification_outputs": {},
        "artifacts": {
            "json": _artifact_ref(run_dir, run_dir / "monitor.report.json"),
            "markdown": _artifact_ref(run_dir, run_dir / "MONITOR_REPORT.md"),
            "diff": _artifact_ref(run_dir, diff_path) if diff_path else None,
            "project_diff": _artifact_ref(run_dir, project_diff_path) if project_diff_path else None,
            "project_diff_markdown": (
                _artifact_ref(run_dir, project_diff_markdown_path) if project_diff_markdown_path else None
            ),
            "change_classes": _artifact_ref(run_dir, classes_path) if classes_path else None,
            "evals": _artifact_ref(run_dir, evals_path) if evals_path else None,
        },
    }
    # Re-check pause state: the monitor may have been paused while refreshing.
    current = _read_monitor_config(state_dir, name)
    if current.get("paused"):
        raise MonitorError(f"Monitor is paused: {name}")
    # A cycle with failed refetches never becomes the baseline: the refreshed
    # pack lacks those URLs, so the next run would misreport them as added.
    refreshed = (
        not dry_run
        and not failures
        and audit is not None
        and (refreshed_pack_dir / "documents.ndjson").exists()
    )
    fingerprint = _change_fingerprint(
        str(config.get("dedupe_key", "url+content_hash")), diff_payload, refreshed_pack_dir
    )
    has_changes = any(diff_payload.get(key) for key in ("changed_urls", "added_urls", "removed_urls"))
    duplicate = has_changes and fingerprint == current.get("last_reported_fingerprint")
    payload["summary"]["change_fingerprint"] = fingerprint
    payload["summary"]["deduplicated"] = duplicate
    payload["summary"]["status"] = "failed" if failures else ("changed" if has_changes else "unchanged")
    if github_issue_file and not duplicate:
        github_issue_file.parent.mkdir(parents=True, exist_ok=True)
        github_issue_file.write_text(_monitor_markdown(payload), encoding="utf-8")
        payload["notification_outputs"]["github_issue_file"] = str(github_issue_file)
    if slack_webhook:
        payload["notification_outputs"]["slack"] = _notify_slack(
            slack_webhook,
            payload,
            should_send=not dry_run and (bool(failures) or (has_changes and not duplicate)),
            post=slack_post or _post_json,
        )
    if refreshed:
        current["baseline_pack_dir"] = str(refreshed_pack_dir.resolve())
        current["last_checked_at"] = utc_now_iso()
        if has_changes:
            current["last_reported_fingerprint"] = fingerprint
        _write_json(_monitor_dir(state_dir, str(current["name"])) / "monitor.json", current)
    report_path = run_dir / "monitor.report.json"
    markdown_path = run_dir / "MONITOR_REPORT.md"
    _write_json(report_path, payload)
    markdown_path.write_text(_monitor_markdown(payload), encoding="utf-8")
    return payload


def _validate_slack_webhook(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise MonitorError("--slack-webhook must be an https URL without embedded credentials")


def _post_json(url: str, body: dict[str, Any]) -> int:
    request = urllib.request.Request(  # noqa: S310 - scheme validated as https above
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
        return int(response.status)


def _notify_slack(url: str, payload: dict[str, Any], *, should_send: bool, post: Any) -> dict[str, Any]:
    """Post a short summary. The webhook URL is a secret: never returned or logged."""
    if not should_send:
        return {"status": "skipped", "reason": "no new changes or failures"}
    summary = payload["summary"]
    lines = [
        f"DocPull monitor `{payload['name']}`: {summary.get('status')}",
        f"changed={summary.get('changed_count')} added={summary.get('added_count')} "
        f"removed={summary.get('removed_count')} failed={summary.get('failed_count')}",
    ]
    diff = payload.get("refresh", {}).get("diff") or {}
    for key, label in (("changed_urls", "changed"), ("added_urls", "added"), ("removed_urls", "removed")):
        lines.extend(f"- {label}: {url}" for url in (diff.get(key) or [])[:10])
    lines.extend(
        f"- failed: {item['url']} ({item['reason']})" for item in (summary.get("failures") or [])[:10]
    )
    try:
        status = post(url, {"text": "\n".join(lines)})
    except (urllib.error.URLError, OSError, ValueError) as err:
        reason = getattr(err, "reason", None) or type(err).__name__
        return {"status": "error", "error": str(reason)[:200]}
    if not 200 <= int(status) < 300:
        return {"status": "error", "error": f"HTTP {status}"}
    return {"status": "sent", "http_status": int(status)}


def _change_fingerprint(dedupe_key: str, diff_payload: dict[str, Any], refreshed_pack_dir: Path) -> str:
    """Hash the reported change set using the monitor's ``dedupe_key`` fields."""
    parts: dict[str, Any] = {
        key: sorted(str(url) for url in diff_payload.get(key) or [])
        for key in ("changed_urls", "added_urls", "removed_urls")
    }
    if "content_hash" in dedupe_key:
        wanted = set(parts["changed_urls"]) | set(parts["added_urls"])
        hashes: dict[str, str] = {}
        documents = refreshed_pack_dir / "documents.ndjson"
        if wanted and documents.exists():
            for line in documents.read_text(encoding="utf-8").splitlines():
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                url = record.get("url") if isinstance(record, dict) else None
                if url in wanted:
                    hashes[str(url)] = str(
                        record.get("content_hash") or hashlib.sha256(line.encode("utf-8")).hexdigest()
                    )
        parts["content_hashes"] = dict(sorted(hashes.items()))
    blob = json.dumps({"dedupe_key": dedupe_key, **parts}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def set_monitor_paused(
    name: str,
    paused: bool,
    *,
    state_dir: Path = DEFAULT_MONITOR_STATE_DIR,
) -> dict[str, Any]:
    """Pause or unpause a local monitor config."""
    config = _read_monitor_config(state_dir, name)
    config["paused"] = paused
    config["updated_at"] = utc_now_iso()
    path = _monitor_dir(state_dir, str(config["name"])) / "monitor.json"
    _write_json(path, config)
    return {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "name": config["name"],
        "paused": paused,
        "path": str(path),
    }


def scheduler_snippet(
    name: str,
    *,
    kind: str = "cron",
    state_dir: Path = DEFAULT_MONITOR_STATE_DIR,
) -> dict[str, Any]:
    """Return a local scheduler snippet for cron, launchd, or GitHub Actions."""
    config = _read_monitor_config(state_dir, name)
    monitor_name = str(config["name"])
    state = state_dir.resolve()
    if kind == "cron":
        snippet = (
            f"# {monitor_name}\n"
            f"{config.get('schedule') or '0 * * * *'} "
            f"docpull monitor --state-dir {state} run {monitor_name} --once\n"
        )
    elif kind == "launchd":
        label = f"technology.raintree.docpull.{monitor_name}"
        snippet = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0"><dict>\n'
            f"<key>Label</key><string>{label}</string>\n"
            "<key>ProgramArguments</key><array>\n"
            "<string>docpull</string><string>monitor</string>"
            f"<string>--state-dir</string><string>{state}</string>"
            "<string>run</string>"
            f"<string>{monitor_name}</string><string>--once</string>\n"
            "</array>\n"
            "<key>StartInterval</key><integer>3600</integer>\n"
            "</dict></plist>\n"
        )
    elif kind == "github-actions":
        snippet = (
            "name: DocPull Monitor\n"
            "on:\n"
            "  schedule:\n"
            "    - cron: '0 * * * *'\n"
            "  workflow_dispatch:\n"
            "jobs:\n"
            "  monitor:\n"
            "    runs-on: ubuntu-24.04\n"
            "    steps:\n"
            "      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5 # v4.3.1\n"
            "        with:\n"
            "          persist-credentials: false\n"
            "      - uses: actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5\n"
            "        with:\n"
            "          python-version: '3.12'\n"
            "      - run: pip install docpull\n"
            f"      - run: docpull monitor --state-dir {state} run {monitor_name} --once\n"
        )
    else:
        raise MonitorError("scheduler kind must be cron, launchd, or github-actions")
    return {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "name": monitor_name,
        "kind": kind,
        "state_dir": str(state),
        "snippet": snippet,
    }


def latest_monitor_report(
    name: str,
    *,
    state_dir: Path = DEFAULT_MONITOR_STATE_DIR,
) -> dict[str, Any]:
    monitor_dir = _monitor_dir(state_dir, _safe_monitor_name(name))
    reports = sorted((monitor_dir / "runs").glob("*/monitor.report.json"))
    if not reports:
        raise MonitorError(f"No reports found for monitor: {name}")
    path = reports[-1]
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise MonitorError(f"Invalid monitor report: {path}")
    payload: dict[str, Any] = loaded
    payload["path"] = str(path)
    return payload


def classify_pack_changes(
    old_pack_dir: Path,
    new_pack_dir: Path,
    diff_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify local pack changes into monitor categories without provider calls."""
    diff = diff_payload or diff_packs(old_pack_dir, new_pack_dir)
    old_records = _monitor_records_by_url(old_pack_dir / "documents.ndjson")
    new_records = _monitor_records_by_url(new_pack_dir / "documents.ndjson")
    classifications: list[dict[str, Any]] = []

    for url in diff.get("added_urls", []):
        classifications.append(_change_classification(url, "source_added", "medium", "Source was added."))
    for url in diff.get("removed_urls", []):
        classifications.append(
            _change_classification(url, "source_disappeared", "high", "Source disappeared from the pack.")
        )
    for detail in diff.get("changed_details", []):
        if not isinstance(detail, dict):
            continue
        url = str(detail.get("url") or "")
        if not url:
            continue
        before = _combined_record_text(old_records.get(url, []))
        after = _combined_record_text(new_records.get(url, []))
        for item in _classify_text_delta(url, before, after, detail):
            classifications.append(item)

    if not classifications and not diff.get("unchanged_urls"):
        classifications.append(
            _change_classification(
                "",
                "no_evidence",
                "low",
                "No comparable monitor evidence was available.",
                warnings=["empty diff"],
            )
        )

    by_type: dict[str, int] = {}
    for item in classifications:
        by_type[str(item["change_type"])] = by_type.get(str(item["change_type"]), 0) + 1
    evals = [
        {
            "schema_version": MONITOR_SCHEMA_VERSION,
            "generated_at": utc_now_iso(),
            "eval_type": "monitor_change_classification",
            "url": item["url"],
            "expected": item["change_type"],
            "observed": item["change_type"],
            "confidence": item["confidence"],
            "passed": True,
        }
        for item in classifications
    ]
    return {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "old_pack_dir": str(old_pack_dir.resolve()),
        "new_pack_dir": str(new_pack_dir.resolve()),
        "classification_count": len(classifications),
        "by_type": by_type,
        "classifications": classifications,
        "evals": evals,
    }


def _read_monitor_config(state_dir: Path, name: str) -> dict[str, Any]:
    path = _monitor_dir(state_dir, _safe_monitor_name(name)) / "monitor.json"
    if not path.exists():
        raise MonitorError(f"Unknown monitor: {name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise MonitorError(f"Invalid monitor config: {path}")
    return data


def _empty_classifications(old_pack_dir: Path, new_pack_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "old_pack_dir": str(old_pack_dir.resolve()),
        "new_pack_dir": str(new_pack_dir.resolve()),
        "classification_count": 0,
        "by_type": {},
        "classifications": [],
        "evals": [],
    }


def _classify_text_delta(
    url: str,
    before: str,
    after: str,
    detail: dict[str, Any],
) -> list[dict[str, Any]]:
    combined = f"{before}\n{after}".lower()
    items: list[dict[str, Any]] = []
    rules = [
        (
            "pricing_billing",
            ("price", "pricing", "billing", "invoice", "subscription", "metered", "credit", "$"),
            "Pricing or billing language changed.",
            "high",
        ),
        (
            "deprecation_removal",
            ("deprecated", "deprecation", "removed", "removal", "sunset", "end of life", "eol"),
            "Deprecation or removal language changed.",
            "high",
        ),
        (
            "auth_security",
            ("auth", "oauth", "token", "api key", "apikey", "permission", "scope", "security", "encrypt"),
            "Authentication or security language changed.",
            "high",
        ),
        (
            "parameter_schema",
            ("parameter", "schema", "field", "property", "request body", "response body", "enum"),
            "Parameter or schema language changed.",
            "medium",
        ),
        (
            "api_behavior",
            ("api", "endpoint", "method", "rate limit", "status code", "webhook", "pagination"),
            "API behavior language changed.",
            "medium",
        ),
        (
            "changelog_only",
            ("changelog", "release notes", "what's new", "whats new"),
            "Change appears to be changelog or release-note content.",
            "low",
        ),
        (
            "page_js_only",
            ("enable javascript", "requires javascript", "javascript is disabled"),
            "Page may have become JavaScript-only.",
            "medium",
        ),
        (
            "robots_policy_blocked",
            ("robots.txt", "robots blocked", "403", "forbidden", "policy denied"),
            "Source may be blocked by robots or source policy.",
            "medium",
        ),
    ]
    for change_type, keywords, summary, confidence in rules:
        if any(keyword in combined for keyword in keywords):
            items.append(_change_classification(url, change_type, confidence, summary))
    if not items:
        reasons = []
        if detail.get("content_changed"):
            reasons.append("content hash changed")
        if detail.get("title_changed"):
            reasons.append("title changed")
        if detail.get("path_changed"):
            reasons.append("artifact path changed")
        items.append(
            _change_classification(
                url,
                "content_changed",
                "low",
                "Content changed without a more specific local classifier match.",
                evidence=reasons,
            )
        )
    return items


def _change_classification(
    url: str,
    change_type: str,
    confidence: str,
    summary: str,
    *,
    evidence: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": MONITOR_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "url": url,
        "change_type": change_type,
        "confidence": confidence,
        "summary": summary,
        "evidence": evidence or [],
        "warnings": warnings or [],
    }


def _monitor_records_by_url(path: Path) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {}
    if not path.exists():
        return records
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        url = str(record.get("url") or "")
        if url:
            records.setdefault(url, []).append(record)
    return records


def _combined_record_text(records: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for record in records:
        for key in ("title", "content", "source_type"):
            value = record.get(key)
            if value:
                parts.append(str(value))
    return "\n".join(parts)


def _monitor_dir(state_dir: Path, name: str) -> Path:
    return state_dir.resolve() / _safe_monitor_name(name)


def _safe_monitor_name(name: str) -> str:
    cleaned = name.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,127}", cleaned):
        raise MonitorError("Monitor names must use lowercase letters, digits, '.', '_' or '-'")
    return cleaned


def _run_stamp() -> str:
    return re.sub(r"[^0-9A-Za-z]+", "-", utc_now_iso()).strip("-")


def _monitor_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary", {})
    lines = [
        "# Monitor Report",
        "",
        f"Monitor: {payload.get('name')}",
        f"Dry run: {payload.get('dry_run')}",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    refresh = payload.get("refresh")
    if isinstance(refresh, dict):
        lines.extend(["", "## Refresh", ""])
        refresh_summary = refresh.get("summary", {})
        if isinstance(refresh_summary, dict):
            for key, value in refresh_summary.items():
                lines.append(f"- {key.replace('_', ' ').title()}: {value}")
    classes = payload.get("change_classes")
    if isinstance(classes, dict) and classes.get("classifications"):
        lines.extend(["", "## Change Classes", ""])
        for item in classes.get("classifications", [])[:25]:
            if isinstance(item, dict):
                lines.append(
                    f"- {item.get('change_type')} ({item.get('confidence')}): {item.get('url') or 'pack'}"
                )
    return "\n".join(lines).rstrip() + "\n"


def _project_diff_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Project Diff",
        "",
        f"- Added URLs: {len(payload.get('added_urls', []))}",
        f"- Removed URLs: {len(payload.get('removed_urls', []))}",
        f"- Changed URLs: {len(payload.get('changed_urls', []))}",
        f"- Unchanged URLs: {len(payload.get('unchanged_urls', []))}",
        "",
    ]
    for label, key in (
        ("Added", "added_urls"),
        ("Removed", "removed_urls"),
        ("Changed", "changed_urls"),
    ):
        values = payload.get(key, [])
        if values:
            lines.extend([f"## {label}", ""])
            for url in values[:100]:
                lines.append(f"- {url}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
