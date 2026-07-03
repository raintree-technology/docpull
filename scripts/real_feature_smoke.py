#!/usr/bin/env python3
"""Run a real-data smoke across DocPull's free/local public surface.

This script intentionally lives outside the default pytest path because it
fetches live public data and can take several minutes. It is designed for
release candidates and scheduled/manual CI jobs.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import sqlite3

# Release smoke executes this checkout's docpull CLI commands.
import subprocess  # nosec B404
import sys
import tempfile
import textwrap
import time
import urllib.parse
import urllib.request
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_TRANSCRIPT_URL = (
    "https://raw.githubusercontent.com/alastairbudge/leonardo-english-vtt/master/ELFCM174.vtt"
)
DEFAULT_OPENAPI_URL = (
    "https://raw.githubusercontent.com/swagger-api/swagger-petstore/master/src/main/resources/openapi.yaml"
)
DEFAULT_DATASET_URL = "https://raw.githubusercontent.com/mwaskom/seaborn-data/master/iris.csv"
DEFAULT_README_URL = "https://raw.githubusercontent.com/psf/requests/main/README.md"


@dataclass
class SmokeResult:
    name: str
    status: str
    classification: str = "required"
    cmd: list[str] | None = None
    cwd: str | None = None
    code: int | None = None
    seconds: float = 0.0
    stdout_tail: str = ""
    stderr_tail: str = ""
    note: str = ""
    artifacts: list[str] = field(default_factory=list)


class RealFeatureSmoke:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.repo = Path(args.repo).resolve()
        self.python = Path(args.python).expanduser()
        self.base = (
            Path(args.base_dir).resolve()
            if args.base_dir
            else Path(tempfile.mkdtemp(prefix="docpull-real-smoke-"))
        )
        self.base.mkdir(parents=True, exist_ok=True)
        self.env = os.environ.copy()
        existing_pythonpath = self.env.get("PYTHONPATH")
        pythonpath = str(self.repo / "src")
        if existing_pythonpath:
            pythonpath = pythonpath + os.pathsep + existing_pythonpath
        self.env.update(
            {
                "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": pythonpath,
                "DOCPULL_CONTACT_EMAIL": self.env.get(
                    "DOCPULL_CONTACT_EMAIL",
                    "docpull-smoke@example.com",
                ),
                "DOCPULL_DOCS_DIR": str(self.base / "mcp-docs"),
                "XDG_CONFIG_HOME": str(self.base / "xdg-config"),
            }
        )
        if args.trust_render_targets:
            self.env["DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS"] = "1"
        self.results: list[SmokeResult] = []

    def command_plan(self) -> list[str]:
        return command_plan_for_args(self.args)

    def run_all(self) -> dict[str, Any]:
        self._download_inputs()
        self._root_fetches()
        packs = self._typed_packs()
        main_pack = packs["package"]
        self._pack_tools(main_pack)
        self._exports(main_pack)
        self._graph_ci_servers(main_pack)
        self._policy_auth_monitor()
        if self.args.strict_ci:
            self._strict_context_ci_fixture()
        if self.args.auth_matrix:
            self._auth_matrix()
        if not self.args.skip_render:
            self._render()
        self._mcp(main_pack)
        if not self.args.skip_project:
            self._project_lifecycle()
            self._watch()
        if self.args.include_cloud:
            self._cloud_render_smokes()
        return self.summary()

    def summary(self) -> dict[str, Any]:
        failures = [
            item for item in self.results if item.status != "pass" and item.classification == "required"
        ]
        payload = {
            "schema_version": 1,
            "base_dir": str(self.base),
            "total": len(self.results),
            "passed": sum(1 for item in self.results if item.status == "pass"),
            "failed_required": len(failures),
            "failed_optional": sum(
                1 for item in self.results if item.status != "pass" and item.classification != "required"
            ),
            "failures": [item.__dict__ for item in failures],
            "results": [item.__dict__ for item in self.results],
        }
        report = self.base / "real_feature_smoke.report.json"
        report.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        payload["report"] = str(report)
        return payload

    def _record(self, item: SmokeResult) -> None:
        self.results.append(item)

    def _record_check(
        self,
        name: str,
        ok: bool,
        *,
        note: str = "",
        artifacts: list[Path | str] | None = None,
        classification: str = "required",
    ) -> None:
        self._record(
            SmokeResult(
                name=name,
                status="pass" if ok else "fail",
                classification=classification,
                cwd=str(self.base),
                note="" if ok else note,
                artifacts=[str(item) for item in artifacts or []],
            )
        )

    def _assert_path_nonempty(
        self,
        name: str,
        path: Path,
        *,
        min_bytes: int = 1,
        classification: str = "required",
    ) -> bool:
        ok = path.exists() and path.stat().st_size >= min_bytes
        note = "" if ok else f"missing or empty artifact: {path}"
        self._record_check(name, ok, note=note, artifacts=[path], classification=classification)
        return ok

    def _read_json_artifact(self, name: str, path: Path) -> Any:
        if not self._assert_path_nonempty(name + " exists", path):
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            self._record_check(name + " valid json", False, note=str(err), artifacts=[path])
            return None
        self._record_check(name + " valid json", True, artifacts=[path])
        return payload

    def _read_ndjson_artifact(self, name: str, path: Path) -> list[dict[str, Any]]:
        if not self._assert_path_nonempty(name + " exists", path):
            return []
        records: list[dict[str, Any]] = []
        try:
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"line {line_number} is not a JSON object")
                records.append(value)
        except (json.JSONDecodeError, ValueError) as err:
            self._record_check(name + " valid ndjson", False, note=str(err), artifacts=[path])
            return []
        self._record_check(
            name + " valid ndjson",
            bool(records),
            note="" if records else "expected at least one NDJSON record",
            artifacts=[path],
        )
        return records

    def _assert_pack_artifacts(
        self,
        pack_dir: Path,
        label: str,
        *,
        output_format: str | None = None,
        expected_url_prefix: str | None = None,
        require_inline_citations: bool = False,
    ) -> None:
        manifest = self._read_json_artifact(f"{label} corpus manifest", pack_dir / "corpus.manifest.json")
        if not isinstance(manifest, dict):
            return
        record_count = _positive_int(manifest.get("record_count")) or _positive_int(
            manifest.get("document_count")
        )
        manifest_records = [item for item in manifest.get("records", []) if isinstance(item, dict)]
        if output_format is not None:
            self._record_check(
                f"{label} manifest output format",
                manifest.get("output_format") == output_format,
                note=f"expected {output_format}, got {manifest.get('output_format')!r}",
                artifacts=[pack_dir / "corpus.manifest.json"],
            )
        self._record_check(
            f"{label} manifest has records",
            bool(record_count and record_count > 0) or bool(manifest_records),
            note="manifest did not report records",
            artifacts=[pack_dir / "corpus.manifest.json"],
        )
        self._assert_path_nonempty(f"{label} sources markdown", pack_dir / "sources.md")
        routes = self._read_json_artifact(f"{label} acquisition routes", pack_dir / "acquisition.routes.json")
        self._record_check(
            f"{label} acquisition routes populated",
            isinstance(routes, dict) and bool(routes.get("routes") or routes.get("records")),
            note="acquisition.routes.json did not contain route records",
            artifacts=[pack_dir / "acquisition.routes.json"],
        )

        records = self._load_pack_records(pack_dir, manifest)
        self._record_check(
            f"{label} readable content records",
            bool(records) and any(str(record.get("content") or "").strip() for record in records),
            note="no readable record content found",
            artifacts=[pack_dir],
        )
        if expected_url_prefix:
            self._record_check(
                f"{label} expected source URL",
                any(str(record.get("url") or "").startswith(expected_url_prefix) for record in records),
                note=f"no record URL started with {expected_url_prefix}",
                artifacts=[pack_dir],
            )
        inline_citations = (
            bool(records)
            and any(record.get("source_citation_id") for record in records)
            and any(record.get("record_citation_id") for record in records)
        )
        manifest_citation_inputs = bool(manifest_records) and bool(records)
        citation_sidecar = self._pack_citation_sidecar_has_records(label, pack_dir)
        citation_ok = inline_citations or citation_sidecar
        if not require_inline_citations:
            citation_ok = citation_ok or manifest_citation_inputs
        self._record_check(
            f"{label} precise citations",
            citation_ok,
            note="" if citation_ok else "records lacked inline citations and citation sidecars",
            artifacts=[pack_dir],
        )
        self._record_check(
            f"{label} route metadata",
            bool(records) and any(isinstance(record.get("route"), dict) for record in records),
            note="records lacked route metadata",
            artifacts=[pack_dir],
        )

    def _load_pack_records(self, pack_dir: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
        ndjson = pack_dir / "documents.ndjson"
        if ndjson.exists():
            return self._read_ndjson_artifact(f"{pack_dir.name} documents", ndjson)
        json_path = pack_dir / "documents.json"
        if json_path.exists():
            payload = self._read_json_artifact(f"{pack_dir.name} documents", json_path)
            if isinstance(payload, list):
                return [item for item in payload if isinstance(item, dict)]
            if isinstance(payload, dict):
                values = payload.get("documents") or payload.get("records")
                if isinstance(values, list):
                    return [item for item in values if isinstance(item, dict)]
        sqlite_path = pack_dir / "documents.db"
        if sqlite_path.exists():
            return self._read_sqlite_records(pack_dir.name, sqlite_path)
        records: list[dict[str, Any]] = []
        for item in manifest.get("records", []):
            if not isinstance(item, dict):
                continue
            output_path = item.get("output_path")
            if not isinstance(output_path, str) or output_path == "-":
                continue
            candidate = (pack_dir / output_path).resolve()
            try:
                candidate.relative_to(pack_dir.resolve())
            except ValueError:
                continue
            if candidate.exists():
                records.append({**item, "content": candidate.read_text(encoding="utf-8", errors="replace")})
        return records

    def _read_sqlite_records(self, name: str, sqlite_path: Path) -> list[dict[str, Any]]:
        if not self._assert_path_nonempty(name + " sqlite exists", sqlite_path):
            return []
        try:
            with sqlite3.connect(sqlite_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = [dict(row) for row in conn.execute("select * from documents limit 25")]
        except sqlite3.Error as err:
            self._record_check(name + " sqlite readable", False, note=str(err), artifacts=[sqlite_path])
            return []
        self._record_check(
            name + " sqlite readable",
            bool(rows),
            note="" if rows else "documents table had no rows",
            artifacts=[sqlite_path],
        )
        for row in rows:
            for key in ("route", "rights", "metadata", "extraction"):
                value = row.get(key)
                if isinstance(value, str) and value.strip().startswith(("{", "[")):
                    with suppress(json.JSONDecodeError):
                        row[key] = json.loads(value)
        return rows

    def _pack_citation_sidecar_has_records(self, label: str, pack_dir: Path) -> bool:
        citation_index = pack_dir / "citation.index.json"
        if citation_index.exists():
            payload = self._read_json_artifact(f"{label} citation index", citation_index)
            entries = payload.get("entries") if isinstance(payload, dict) else None
            ok = isinstance(entries, list) and any(
                isinstance(entry, dict)
                and entry.get("citation_id")
                and entry.get("record_citation_id")
                and entry.get("document_id")
                for entry in entries
            )
            self._record_check(
                f"{label} citation index records",
                ok,
                note="" if ok else "citation.index.json lacked record citation entries",
                artifacts=[citation_index],
            )
            return ok

        citations = pack_dir / "citations.json"
        if citations.exists():
            payload = self._read_json_artifact(f"{label} citations sidecar", citations)
            sources = payload.get("sources") if isinstance(payload, dict) else None
            ok = isinstance(sources, list) and any(
                isinstance(source, dict)
                and source.get("citation_id")
                and isinstance(source.get("record_citations"), list)
                and source.get("record_citations")
                for source in sources
            )
            self._record_check(
                f"{label} citations sidecar records",
                ok,
                note="" if ok else "citations.json lacked record citation entries",
                artifacts=[citations],
            )
            return ok

        return False

    def _assert_command_json(
        self,
        name: str,
        completed: subprocess.CompletedProcess[str],
        *,
        required_keys: set[str] | None = None,
    ) -> dict[str, Any] | None:
        if completed.returncode != 0:
            self._record_check(name + " stdout json", False, note="command failed")
            return None
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as err:
            self._record_check(name + " stdout json", False, note=str(err))
            return None
        ok = isinstance(payload, dict) and all(key in payload for key in required_keys or set())
        self._record_check(
            name + " stdout json",
            ok,
            note="" if ok else f"missing expected keys: {sorted(required_keys or set())}",
        )
        return payload if isinstance(payload, dict) else None

    def _assert_export_artifact(self, output_format: str, output: Path) -> None:
        if output_format in {"codex-skill", "claude-skill"}:
            self._assert_path_nonempty(f"export {output_format} skill manifest", output / "SKILL.md")
            self._record_check(
                f"export {output_format} references",
                (output / "references").exists(),
                note="references directory missing",
                artifacts=[output / "references"],
            )
            return
        if output_format == "cursor-rules" and output.suffix != ".mdc":
            output = output / "real-package-smoke.mdc"
        if not self._assert_path_nonempty(f"export {output_format} artifact", output):
            return
        if output_format.endswith("-jsonl") or output_format == "warehouse-ndjson":
            records = self._read_ndjson_artifact(f"export {output_format}", output)
            self._record_check(
                f"export {output_format} provenance",
                bool(records)
                and any(
                    _record_has_provenance(record)
                    or _record_has_provenance(_dict_value(record.get("metadata")))
                    for record in records
                ),
                note="export records lacked citation provenance",
                artifacts=[output],
            )
            return
        if output_format in {"n8n-json", "vercel-ai-json", "crewai-json"}:
            payload = self._read_json_artifact(f"export {output_format}", output)
            self._record_check(
                f"export {output_format} consumer payload",
                isinstance(payload, dict) and bool(payload),
                note="expected non-empty JSON object",
                artifacts=[output],
            )
            return
        if output_format in {"sheets-csv", "sheets-tsv"}:
            delimiter = "," if output_format == "sheets-csv" else "\t"
            try:
                with output.open(encoding="utf-8", newline="") as fp:
                    rows = list(csv.DictReader(fp, delimiter=delimiter))
            except csv.Error as err:
                self._record_check(f"export {output_format} parse", False, note=str(err), artifacts=[output])
                return
            self._record_check(
                f"export {output_format} parse",
                bool(rows) and "record_citation_id" in rows[0],
                note="expected tabular rows with record_citation_id",
                artifacts=[output],
            )
            return
        if output_format == "parquet":
            self._assert_path_nonempty("export parquet non-empty", output, min_bytes=16)

    def _assert_citations_artifact(self, label: str, path: Path) -> None:
        payload = self._read_json_artifact(f"{label} citations", path)
        sources = payload.get("sources") if isinstance(payload, dict) else None
        record_citations: list[dict[str, Any]] = []
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict) and isinstance(source.get("record_citations"), list):
                    record_citations.extend(
                        item for item in source["record_citations"] if isinstance(item, dict)
                    )
        self._record_check(
            f"{label} citation records",
            bool(record_citations) and all(item.get("record_citation_id") for item in record_citations),
            note="citation payload lacked record_citation_id entries",
            artifacts=[path],
        )

    def _docpull(
        self,
        name: str,
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 180,
        classification: str = "required",
        allow_codes: set[int] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return self._run(
            name,
            [str(self.python), "-m", "docpull", *args],
            cwd=cwd or self.repo,
            timeout=timeout,
            classification=classification,
            allow_codes=allow_codes,
        )

    def _run(
        self,
        name: str,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: int,
        classification: str = "required",
        allow_codes: set[int] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        allowed = allow_codes or {0}
        started = time.time()
        try:
            completed = subprocess.run(  # nosec B603
                cmd,
                cwd=cwd,
                env=self.env,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as err:
            stdout = err.stdout if isinstance(err.stdout, str) else ""
            stderr = err.stderr if isinstance(err.stderr, str) else ""
            self._record(
                SmokeResult(
                    name=name,
                    status="fail",
                    classification=classification,
                    cmd=cmd,
                    cwd=str(cwd),
                    seconds=round(time.time() - started, 3),
                    stdout_tail=stdout[-3000:],
                    stderr_tail=stderr[-3000:],
                    note=f"timeout after {timeout}s",
                )
            )
            return subprocess.CompletedProcess(cmd, 124, stdout, stderr)
        status = "pass" if completed.returncode in allowed else "fail"
        stdout_tail = "" if status == "pass" else completed.stdout[-3000:]
        stderr_tail = "" if status == "pass" else completed.stderr[-3000:]
        self._record(
            SmokeResult(
                name=name,
                status=status,
                classification=classification,
                cmd=cmd,
                cwd=str(cwd),
                code=completed.returncode,
                seconds=round(time.time() - started, 3),
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
        )
        return completed

    def _download(self, url: str, path: Path) -> None:
        started = time.time()
        _require_allowed_smoke_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": "docpull-real-feature-smoke/1.0"})
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(request, timeout=60) as response:  # nosec B310
                path.write_bytes(response.read())
        except OSError as err:
            self._record(
                SmokeResult(
                    name=f"download {path.name}",
                    status="fail",
                    cmd=["download", url, str(path)],
                    cwd=str(self.base),
                    seconds=round(time.time() - started, 3),
                    stderr_tail=str(err),
                )
            )
            return
        self._record(
            SmokeResult(
                name=f"download {path.name}",
                status="pass",
                cmd=["download", url, str(path)],
                cwd=str(self.base),
                seconds=round(time.time() - started, 3),
                artifacts=[str(path)],
            )
        )

    def _download_inputs(self) -> None:
        self._download(DEFAULT_README_URL, self.base / "inputs" / "requests.README.md")
        self._download(DEFAULT_DATASET_URL, self.base / "inputs" / "iris.csv")

    def _validate(self, pack_dir: Path, level: str, label: str) -> None:
        self._docpull(
            f"validate {label} {level}",
            ["pack", "validate", str(pack_dir), "--level", level, "--format", "json"],
            timeout=90,
        )

    def _prepare_eval(self, pack_dir: Path, label: str, objective: str) -> None:
        self._docpull(
            f"prepare eval {label}",
            [
                "pack",
                "prepare",
                str(pack_dir),
                "--eval-grade",
                "--objective",
                objective,
                "--search-query",
                objective,
            ],
            timeout=180,
        )
        self._validate(pack_dir, "eval", label)

    def _root_fetches(self) -> None:
        url = "https://www.python.org/blogs/"
        formats = ["markdown", "json", "ndjson", "sqlite", "okf"]
        if self.args.quick:
            formats = ["markdown", "ndjson"]
        for output_format in formats:
            output = self.base / "root-fetch" / output_format
            self._docpull(
                f"root fetch {output_format}",
                [url, "--single", "-o", str(output), "-f", output_format, "--quiet"],
                timeout=180,
            )
            self._validate(output, "raw", f"root-{output_format}")
            self._assert_pack_artifacts(
                output,
                f"root fetch {output_format}",
                output_format=output_format,
                expected_url_prefix=url,
            )
            citations_output = self.base / "root-fetch" / f"{output_format}.citations.json"
            self._docpull(
                f"root fetch {output_format} citations",
                ["pack", "citations", str(output), "--output", str(citations_output)],
                timeout=90,
            )
            self._assert_citations_artifact(f"root fetch {output_format}", citations_output)

    def _typed_packs(self) -> dict[str, Path]:
        packs = self.base / "packs"
        readme = self.base / "inputs" / "requests.README.md"
        dataset = self.base / "inputs" / "iris.csv"
        commands: dict[str, list[str]] = {
            "parse": [
                "parse",
                str(readme),
                "-o",
                str(packs / "parse"),
                "--backend",
                "text",
                "--eval-grade",
                "--format",
                "json",
                "--title",
                "Requests README",
                "--source-url",
                "https://github.com/psf/requests/blob/main/README.md",
            ],
            "openapi": ["openapi-pack", DEFAULT_OPENAPI_URL, "-o", str(packs / "openapi"), "--json"],
            "feed": [
                "feed-pack",
                "https://blog.python.org/feeds/posts/default",
                "-o",
                str(packs / "feed"),
                "--max-items",
                "3",
                "--json",
            ],
            "paper": [
                "paper-pack",
                "arxiv:1706.03762",
                "-o",
                str(packs / "paper"),
                "--max-items",
                "3",
                "--eval-grade",
                "--json",
            ],
            "repo": [
                "repo-pack",
                "psf/requests",
                "-o",
                str(packs / "repo"),
                "--max-items",
                "5",
                "--eval-grade",
                "--json",
            ],
            "package": [
                "package-pack",
                "pypi:requests",
                "-o",
                str(packs / "package"),
                "--max-items",
                "5",
                "--eval-grade",
                "--json",
            ],
            "standards": [
                "standards-pack",
                "rfc:9110",
                "-o",
                str(packs / "standards"),
                "--max-items",
                "5",
                "--eval-grade",
                "--json",
            ],
            "dataset": [
                "dataset-pack",
                str(dataset),
                "-o",
                str(packs / "dataset"),
                "--max-items",
                "5",
                "--eval-grade",
                "--json",
            ],
            "transcript": [
                "transcript-pack",
                DEFAULT_TRANSCRIPT_URL,
                "-o",
                str(packs / "transcript"),
                "--max-items",
                "5",
                "--eval-grade",
                "--json",
            ],
            "wiki": [
                "wiki-pack",
                "wiki:Web_scraping",
                "-o",
                str(packs / "wiki"),
                "--max-items",
                "5",
                "--eval-grade",
                "--json",
            ],
        }
        if self.args.quick:
            commands = {key: commands[key] for key in ("parse", "package", "wiki")}
        pack_paths: dict[str, Path] = {}
        for label, command in commands.items():
            pack_dir = packs / label
            self._docpull(f"{label} real data", command, timeout=240)
            if label in {"openapi", "feed"}:
                self._validate(pack_dir, "raw", label)
                self._prepare_eval(pack_dir, label, f"{label} real-data smoke")
            else:
                self._validate(pack_dir, "eval", label)
            self._assert_pack_artifacts(pack_dir, f"{label} pack", require_inline_citations=True)
            pack_paths[label] = pack_dir
        if "package" not in pack_paths:
            raise RuntimeError("quick/full smoke must include package pack")
        return pack_paths

    def _pack_tools(self, pack_dir: Path) -> None:
        artifacts = self.base / "artifacts"
        commands = [
            ("score", ["pack", "score", str(pack_dir), "--output", str(artifacts / "score.json")]),
            (
                "audit",
                [
                    "pack",
                    "audit",
                    str(pack_dir),
                    "--json",
                    "--redaction",
                    "--output",
                    str(artifacts / "audit.json"),
                ],
            ),
            ("publish", ["pack", "publish", str(pack_dir), "--target", "agent-docs"]),
            ("basis", ["pack", "basis", str(pack_dir), "--claim", "Requests is a Python HTTP library."]),
            ("search", ["pack", "search", str(pack_dir), "HTTP", "--output", str(artifacts / "search.json")]),
            ("brief", ["pack", "brief", str(pack_dir), "--objective", "Summarize package context"]),
            ("entities", ["pack", "entities", str(pack_dir), "--output", str(artifacts / "entities.json")]),
            ("sources", ["pack", "sources", str(pack_dir), "--output", str(artifacts / "sources.json")]),
            (
                "citations",
                ["pack", "citations", str(pack_dir), "--output", str(artifacts / "citations.json")],
            ),
            ("redact", ["pack", "redact", str(pack_dir), "-o", str(self.base / "redacted" / "package")]),
            (
                "refresh dry-run",
                ["refresh", str(pack_dir), "--dry-run", "-o", str(self.base / "refreshed" / "package")],
            ),
            (
                "diff self",
                ["pack", "diff", str(pack_dir), str(pack_dir), "--output", str(artifacts / "diff.json")],
            ),
        ]
        for label, command in commands:
            self._docpull(f"pack {label}", command, timeout=120)
        for name in ("score", "audit", "search", "entities", "sources", "citations", "diff"):
            self._read_json_artifact(f"pack {name} artifact", artifacts / f"{name}.json")
        self._assert_path_nonempty(
            "pack redacted documents", self.base / "redacted" / "package" / "documents.ndjson"
        )
        self._assert_path_nonempty("pack publish artifact", pack_dir / "AGENT_CONTEXT.md")

    def _exports(self, pack_dir: Path) -> None:
        export_dir = self.base / "exports"
        suffixes = {
            "openai-vector-jsonl": ".jsonl",
            "langchain-jsonl": ".jsonl",
            "llamaindex-jsonl": ".jsonl",
            "dspy-jsonl": ".jsonl",
            "n8n-json": ".json",
            "vercel-ai-json": ".json",
            "crewai-json": ".json",
            "sheets-csv": ".csv",
            "sheets-tsv": ".tsv",
            "warehouse-ndjson": ".ndjson",
            "parquet": ".parquet",
        }
        formats = [
            "openai-vector-jsonl",
            "langchain-jsonl",
            "llamaindex-jsonl",
            "dspy-jsonl",
            "codex-skill",
            "claude-skill",
            "cursor-rules",
            "n8n-json",
            "vercel-ai-json",
            "crewai-json",
            "sheets-csv",
            "sheets-tsv",
            "warehouse-ndjson",
            "parquet",
        ]
        for output_format in formats:
            output = export_dir / (output_format + suffixes.get(output_format, ""))
            self._docpull(
                f"export {output_format}",
                [
                    "export",
                    str(pack_dir),
                    "--format",
                    output_format,
                    "--output",
                    str(output),
                    "--skill-name",
                    "real-package-smoke",
                    "--skill-description",
                    "Real package smoke export",
                ],
                timeout=120,
            )
            self._assert_export_artifact(output_format, output)

    def _graph_ci_servers(self, pack_dir: Path) -> None:
        graph_outputs: dict[str, subprocess.CompletedProcess[str]] = {}
        for label, command in [
            ("build", ["graph", "build", str(pack_dir), "--entity-limit", "25"]),
            ("status", ["graph", "status", str(pack_dir)]),
            ("query", ["graph", "query", str(pack_dir), "requests", "--limit", "5"]),
            ("neighbors", ["graph", "neighbors", str(pack_dir), "requests", "--limit", "5"]),
            ("refresh", ["graph", "refresh", str(pack_dir), "--entity-limit", "25"]),
        ]:
            graph_outputs[label] = self._docpull(f"graph {label}", command, timeout=90)
        graph_json = self._read_json_artifact("graph metadata", pack_dir / "graph.json")
        nodes = self._read_ndjson_artifact("graph nodes", pack_dir / "graph.nodes.ndjson")
        edges = self._read_ndjson_artifact("graph edges", pack_dir / "graph.edges.ndjson")
        self._record_check(
            "graph nodes edges citations",
            isinstance(graph_json, dict)
            and bool(nodes)
            and bool(edges)
            and any(node.get("citation_id") for node in nodes)
            and any(edge.get("citation_id") for edge in edges),
            note="graph sidecars lacked cited nodes/edges",
            artifacts=[
                pack_dir / "graph.json",
                pack_dir / "graph.nodes.ndjson",
                pack_dir / "graph.edges.ndjson",
            ],
        )
        self._record_check(
            "graph query returned results",
            "results" in graph_outputs["query"].stdout.lower(),
            note="graph query output did not report results",
        )
        ci = self._docpull("ci pack", ["ci", str(pack_dir), "--prepare", "--json"], timeout=180)
        ci_payload = self._assert_command_json("ci pack", ci, required_keys={"summary", "gates", "passed"})
        if self.args.strict_ci and isinstance(ci_payload, dict):
            summary = _dict_value(ci_payload.get("summary"))
            self._record_check(
                "strict context ci pack no failures",
                bool(ci_payload.get("passed")) and int(summary.get("fail_count") or 0) == 0,
                note=f"warn_count={summary.get('warn_count')} fail_count={summary.get('fail_count')}",
            )
        self._server("serve pack", ["serve", str(pack_dir), "--port", "{port}"])
        self._server("share report", ["share", str(pack_dir / "PACK_CARD.md"), "--port", "{port}"])
        self._graph_fixture()

    def _policy_auth_monitor(self) -> None:
        policy = self.base / "policy.yml"
        policy.write_text(
            "\n".join(
                [
                    "schema_version: 1",
                    "allowed_domains:",
                    "  - python.org",
                    "  - pypi.org",
                    "providers:",
                    "  allowed:",
                    "    - local",
                    "budget:",
                    "  maximum_paid_cost_usd: 0",
                    "redaction:",
                    "  enabled: true",
                    "  backend: regex",
                    "  patterns:",
                    "    - name: email",
                    '      regex: "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self._docpull("policy validate", ["policy", "validate", str(policy), "--json"])
        self._docpull("policy explain", ["policy", "explain", str(policy), "--json"])
        self._docpull(
            "policy redaction init",
            ["policy", "redaction", "init", "-o", str(self.base / "redaction.yml"), "--json"],
        )
        self._docpull(
            "auth check public header",
            [
                "auth",
                "check",
                "https://www.python.org/blogs/",
                "--auth-policy",
                "public-token-only",
                "--auth-header",
                "X-DocPull-Smoke",
                "real-data",
                "--json",
                "--output",
                str(self.base / "auth.audit.json"),
            ],
            timeout=120,
        )
        auth_audit = self._read_json_artifact("auth public audit", self.base / "auth.audit.json")
        self._record_check(
            "auth public audit non-secret",
            isinstance(auth_audit, dict)
            and auth_audit.get("auth_type") == "header"
            and "real-data" not in json.dumps(auth_audit),
            note="auth audit leaked credential value or wrong auth_type",
            artifacts=[self.base / "auth.audit.json"],
        )
        state = self.base / "monitor-state"
        pack = self.base / "packs" / "package"
        for label, command in [
            (
                "init",
                [
                    "monitor",
                    "--state-dir",
                    str(state),
                    "init",
                    str(pack),
                    "--name",
                    "real-smoke",
                    "--schedule",
                    "manual",
                ],
            ),
            ("list", ["monitor", "--state-dir", str(state), "list", "--json"]),
            (
                "run dry",
                ["monitor", "--state-dir", str(state), "run", "real-smoke", "--once", "--dry-run", "--json"],
            ),
            (
                "trigger dry",
                ["monitor", "--state-dir", str(state), "trigger", "real-smoke", "--dry-run", "--json"],
            ),
            ("pause", ["monitor", "--state-dir", str(state), "pause", "real-smoke", "--json"]),
            ("unpause", ["monitor", "--state-dir", str(state), "unpause", "real-smoke", "--json"]),
            (
                "scheduler",
                [
                    "monitor",
                    "--state-dir",
                    str(state),
                    "scheduler-snippet",
                    "real-smoke",
                    "--kind",
                    "cron",
                    "--json",
                ],
            ),
            ("report", ["monitor", "--state-dir", str(state), "report", "real-smoke", "--json"]),
        ]:
            self._docpull(f"monitor {label}", command, timeout=120)
        self._assert_path_nonempty("monitor config", state / "real-smoke" / "monitor.json")
        self._record_check(
            "monitor reports written",
            bool(list((state / "real-smoke" / "runs").glob("*/monitor.report.json"))),
            note="no monitor.report.json files found",
            artifacts=[state / "real-smoke" / "runs"],
        )
        if self.args.monitor_soak_minutes:
            self._monitor_soak(state)

    def _monitor_soak(self, state: Path) -> None:
        duration = max(0.0, float(self.args.monitor_soak_minutes) * 60.0)
        if duration <= 0:
            return
        deadline = time.time() + duration
        iterations = 0
        while True:
            iterations += 1
            trigger = self._docpull(
                f"monitor soak trigger {iterations}",
                ["monitor", "--state-dir", str(state), "trigger", "real-smoke", "--dry-run", "--json"],
                timeout=120,
            )
            self._assert_command_json(
                f"monitor soak trigger {iterations}", trigger, required_keys={"summary"}
            )
            report = self._docpull(
                f"monitor soak report {iterations}",
                ["monitor", "--state-dir", str(state), "report", "real-smoke", "--json"],
                timeout=120,
            )
            self._assert_command_json(f"monitor soak report {iterations}", report, required_keys={"summary"})
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(30.0, remaining))
        self._record_check(
            "monitor bounded soak completed",
            iterations >= 1,
            note=f"iterations={iterations}",
            artifacts=[state / "real-smoke" / "runs"],
        )

    def _auth_matrix(self) -> None:
        port = _free_port()
        server_script = textwrap.dedent(
            """
            import base64
            import sys
            from http.server import BaseHTTPRequestHandler, HTTPServer

            EXPECTED = {
                "/bearer": ("authorization", "Bearer smoke-bearer-token"),
                "/basic": (
                    "authorization",
                    "Basic " + base64.b64encode(b"smoke-user:smoke-pass").decode("ascii"),
                ),
                "/cookie": ("cookie", "session=smoke-cookie"),
                "/header": ("x-docpull-smoke", "matrix-token"),
            }

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == "/health":
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b"ok")
                        return
                    expected = EXPECTED.get(self.path)
                    if expected is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                    name, value = expected
                    actual = self.headers.get(name, "")
                    if self.path == "/cookie":
                        ok = value in actual
                    else:
                        ok = actual == value
                    self.send_response(200 if ok else 401)
                    self.send_header("content-type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"authorized" if ok else b"unauthorized")

                def log_message(self, *_args):
                    return

            HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
            """
        )
        process = subprocess.Popen(  # nosec B603
            [str(self.python), "-c", server_script, str(port)],
            cwd=self.repo,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            health = f"http://127.0.0.1:{port}/health"
            for _ in range(100):
                if process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=5)
                    self._record(
                        SmokeResult(
                            name="auth matrix fixture",
                            status="fail",
                            cmd=[str(self.python), "-c", "auth-matrix-server", str(port)],
                            cwd=str(self.repo),
                            code=process.returncode,
                            stdout_tail=(stdout or "")[-1000:],
                            stderr_tail=(stderr or "")[-1000:],
                            note="fixture exited before health check",
                        )
                    )
                    return
                try:
                    _url_ok(health)
                    break
                except OSError:
                    time.sleep(0.1)
            else:
                self._record_check("auth matrix fixture", False, note="health check timeout")
                return
            self._record_check("auth matrix fixture", True, artifacts=[health])
            matrix = [
                (
                    "bearer",
                    "/bearer",
                    ["--auth-bearer", "smoke-bearer-token"],
                    ["--auth-bearer", "wrong-bearer-token"],
                    "smoke-bearer-token",
                ),
                (
                    "basic",
                    "/basic",
                    ["--auth-basic", "smoke-user:smoke-pass"],
                    ["--auth-basic", "smoke-user:wrong-pass"],
                    "smoke-pass",
                ),
                (
                    "cookie",
                    "/cookie",
                    ["--auth-cookie", "session=smoke-cookie"],
                    ["--auth-cookie", "session=wrong-cookie"],
                    "smoke-cookie",
                ),
                (
                    "header",
                    "/header",
                    ["--auth-header", "X-DocPull-Smoke", "matrix-token"],
                    ["--auth-header", "X-DocPull-Smoke", "wrong-token"],
                    "matrix-token",
                ),
            ]
            for label, path, ok_args, bad_args, secret in matrix:
                url = f"http://127.0.0.1:{port}{path}"
                output = self.base / "auth-matrix" / f"{label}.json"
                completed = self._docpull(
                    f"auth matrix {label}",
                    [
                        "auth",
                        "check",
                        url,
                        "--auth-policy",
                        "public-token-only",
                        *ok_args,
                        "--allow-insecure-local-http",
                        "--json",
                        "--output",
                        str(output),
                    ],
                    timeout=60,
                )
                payload = self._read_json_artifact(f"auth matrix {label}", output)
                self._record_check(
                    f"auth matrix {label} passes",
                    completed.returncode == 0 and isinstance(payload, dict) and payload.get("ok") is True,
                    note="expected ok=true",
                    artifacts=[output],
                )
                self._record_check(
                    f"auth matrix {label} no secret leak",
                    secret not in (completed.stdout + completed.stderr + json.dumps(payload)),
                    note="credential appeared in CLI output or audit payload",
                    artifacts=[output],
                )
                bad_output = self.base / "auth-matrix" / f"{label}.wrong.json"
                bad = self._docpull(
                    f"auth matrix {label} wrong credential",
                    [
                        "auth",
                        "check",
                        url,
                        "--auth-policy",
                        "public-token-only",
                        *bad_args,
                        "--allow-insecure-local-http",
                        "--json",
                        "--output",
                        str(bad_output),
                    ],
                    timeout=60,
                    allow_codes={1},
                )
                bad_payload = self._read_json_artifact(f"auth matrix {label} wrong", bad_output)
                self._record_check(
                    f"auth matrix {label} wrong fails",
                    bad.returncode == 1
                    and isinstance(bad_payload, dict)
                    and bad_payload.get("ok") is False
                    and bad_payload.get("status_code") == 401,
                    note="expected ok=false status_code=401",
                    artifacts=[bad_output],
                )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def _strict_context_ci_fixture(self) -> None:
        fixture = self.base / "strict-ci-pack"
        self._write_graph_fixture_pack(fixture)
        self._docpull(
            "strict ci fixture prepare",
            [
                "pack",
                "prepare",
                str(fixture),
                "--eval-grade",
                "--objective",
                "Alpha API returns cited JSON results.",
                "--search-query",
                "Alpha API",
            ],
            timeout=120,
        )
        completed = self._docpull(
            "strict ci fixture", ["ci", str(fixture), "--prepare", "--json"], timeout=180
        )
        payload = self._assert_command_json(
            "strict ci fixture",
            completed,
            required_keys={"summary", "gates", "passed"},
        )
        summary = _dict_value(payload.get("summary")) if isinstance(payload, dict) else {}
        self._record_check(
            "strict ci fixture zero warnings",
            completed.returncode == 0
            and isinstance(payload, dict)
            and payload.get("passed") is True
            and int(summary.get("warn_count") or 0) == 0,
            note=f"warn_count={summary.get('warn_count')} fail_count={summary.get('fail_count')}",
            artifacts=[fixture / "context-ci.report.json"],
        )

    def _graph_fixture(self) -> None:
        fixture = self.base / "graph-fixture"
        self._write_graph_fixture_pack(fixture)
        self._docpull(
            "graph fixture build", ["graph", "build", str(fixture), "--entity-limit", "20"], timeout=90
        )
        graph_json = self._read_json_artifact("graph fixture metadata", fixture / "graph.json")
        nodes = self._read_ndjson_artifact("graph fixture nodes", fixture / "graph.nodes.ndjson")
        edges = self._read_ndjson_artifact("graph fixture edges", fixture / "graph.edges.ndjson")
        self._record_check(
            "graph fixture expected entity",
            any(
                node.get("type") == "entity" and node.get("normalized") == "support@example.com"
                for node in nodes
            ),
            note="support@example.com entity missing",
            artifacts=[fixture / "graph.nodes.ndjson"],
        )
        self._record_check(
            "graph fixture expected cited edge",
            any(edge.get("relationship") == "returns" and edge.get("citation_id") for edge in edges),
            note="returns edge with citation missing",
            artifacts=[fixture / "graph.edges.ndjson"],
        )
        query = self._docpull("graph fixture query", ["graph", "query", str(fixture), "support@example.com"])
        neighbors = self._docpull(
            "graph fixture neighbors", ["graph", "neighbors", str(fixture), "support@example.com"]
        )
        refresh = self._docpull(
            "graph fixture refresh", ["graph", "refresh", str(fixture), "--entity-limit", "20"]
        )
        self._record_check(
            "graph fixture exact query outputs",
            query.returncode == 0 and neighbors.returncode == 0 and refresh.returncode == 0,
            note="query/neighbors/refresh failed",
            artifacts=[fixture / "graph.diff.json"],
        )
        self._record_check(
            "graph fixture idempotent refresh",
            isinstance(graph_json, dict)
            and (fixture / "graph.diff.json").exists()
            and _dict_value(
                _dict_value(self._read_json_artifact("graph fixture diff", fixture / "graph.diff.json")).get(
                    "summary"
                )
            ).get("removed_node_count")
            == 0,
            note="refresh removed nodes in deterministic fixture",
            artifacts=[fixture / "graph.diff.json"],
        )

    def _write_graph_fixture_pack(self, pack_dir: Path) -> None:
        pack_dir.mkdir(parents=True, exist_ok=True)
        sources_dir = pack_dir / "sources"
        sources_dir.mkdir(exist_ok=True)
        records = [
            {
                "schema_version": 3,
                "document_id": "doc_alpha_search",
                "chunk_id": "chunk_alpha_search_1",
                "url": "https://docs.example.test/alpha/search",
                "title": "Alpha Search API",
                "content": (
                    "Alpha Search API version 6.0 returns cited JSON results for agent search. "
                    "Contact support@example.com for access."
                ),
                "content_hash": "hash_alpha_search_1",
                "source_type": "fixture",
                "source_citation_id": "S1",
                "record_citation_id": "S1.1",
                "chunk_index": 0,
                "chunk_heading": "Search",
                "route": {"name": "fixture", "status_code": 200, "output_format": "ndjson"},
                "rights": {"status": "allowed", "allowed_use": {"eval_generation": "allowed"}},
                "metadata": {"item_citation_id": "S1.1"},
            },
            {
                "schema_version": 3,
                "document_id": "doc_alpha_extract",
                "chunk_id": "chunk_alpha_extract_1",
                "url": "https://docs.example.test/alpha/extract",
                "title": "Alpha Extract API",
                "content": "Alpha Extract API turns known URLs into markdown context packs.",
                "content_hash": "hash_alpha_extract_1",
                "source_type": "fixture",
                "source_citation_id": "S2",
                "record_citation_id": "S2.1",
                "chunk_index": 0,
                "chunk_heading": "Extract",
                "route": {"name": "fixture", "status_code": 200, "output_format": "ndjson"},
                "rights": {"status": "allowed", "allowed_use": {"eval_generation": "allowed"}},
                "metadata": {"item_citation_id": "S2.1"},
            },
        ]
        for record, count in zip(records, (18, 10), strict=True):
            record["token_count"] = count
        for index, record in enumerate(records, start=1):
            (sources_dir / f"{index:02d}.md").write_text(str(record["content"]), encoding="utf-8")
        (pack_dir / "documents.ndjson").write_text(
            "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n",
            encoding="utf-8",
        )
        manifest = {
            "schema_version": 3,
            "output_format": "ndjson",
            "document_count": len({record["document_id"] for record in records}),
            "record_count": len(records),
            "records": [
                {
                    "document_id": record["document_id"],
                    "chunk_id": record["chunk_id"],
                    "url": record["url"],
                    "title": record["title"],
                    "content_hash": record["content_hash"],
                    "output_path": f"sources/{index:02d}.md",
                    "source_citation_id": record["source_citation_id"],
                    "record_citation_id": record["record_citation_id"],
                }
                for index, record in enumerate(records, start=1)
            ],
        }
        (pack_dir / "corpus.manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (pack_dir / "sources.md").write_text(
            "# Context Pack Sources\n\n1. [Alpha Search API](https://docs.example.test/alpha/search)\n"
            "2. [Alpha Extract API](https://docs.example.test/alpha/extract)\n",
            encoding="utf-8",
        )
        (pack_dir / "acquisition.routes.json").write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "routes": [
                        {
                            "route": "fixture",
                            "output_format": "ndjson",
                            "fetched_count": len(records),
                            "record_count": len(records),
                        }
                    ],
                    "domains": {"docs.example.test": len(records)},
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def _render(self) -> None:
        check = self._docpull("render check local", ["render", "--check", "--runtime", "local"], timeout=60)
        self._docpull(
            "render live local",
            [
                "render",
                "https://example.com",
                "--runtime",
                "local",
                "--live-smoke",
                "-o",
                str(self.base / "render"),
                "--allowed-domain",
                "example.com",
                "--quiet",
            ],
            timeout=120,
        )
        if check.returncode == 0:
            self._render_js_fixture()

    def _render_js_fixture(self) -> None:
        port = _free_port()
        server_script = textwrap.dedent(
            """
            import sys
            from http.server import BaseHTTPRequestHandler, HTTPServer

            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == "/health":
                        self.send_response(200)
                        self.end_headers()
                        self.wfile.write(b"ok")
                        return
                    self.send_response(200)
                    self.send_header("content-type", "text/html")
                    self.end_headers()
                    self.wfile.write(b'''<!doctype html>
                    <html><head><title>DocPull Render Fixture</title></head>
                    <body><main id="app">Before JS</main>
                    <script>
                    document.getElementById("app").textContent = "DocPull JS render fixture ready";
                    </script></body></html>''')

                def log_message(self, *_args):
                    return

            HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
            """
        )
        process = subprocess.Popen(  # nosec B603
            [str(self.python), "-c", server_script, str(port)],
            cwd=self.repo,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        old_value = self.env.get("DOCPULL_RENDER_ALLOW_LOCAL_TARGETS")
        self.env["DOCPULL_RENDER_ALLOW_LOCAL_TARGETS"] = "1"
        try:
            health = f"http://127.0.0.1:{port}/health"
            for _ in range(100):
                try:
                    _url_ok(health)
                    break
                except OSError:
                    if process.poll() is not None:
                        stdout, stderr = process.communicate(timeout=5)
                        self._record(
                            SmokeResult(
                                name="render js fixture",
                                status="fail",
                                cmd=[str(self.python), "-c", "render-js-server", str(port)],
                                cwd=str(self.repo),
                                code=process.returncode,
                                stdout_tail=(stdout or "")[-1000:],
                                stderr_tail=(stderr or "")[-1000:],
                                note="fixture exited before health check",
                            )
                        )
                        return
                    time.sleep(0.1)
            else:
                self._record_check("render js fixture", False, note="health check timeout")
                return
            output = self.base / "render-js"
            url = f"http://127.0.0.1:{port}/"
            completed = self._docpull(
                "render js loopback local",
                [
                    "render",
                    url,
                    "--runtime",
                    "local",
                    "--live-smoke",
                    "-o",
                    str(output),
                    "--allowed-domain",
                    "127.0.0.1",
                    "--quiet",
                ],
                timeout=120,
            )
            rendered_text = ""
            for html_path in output.glob("*.html"):
                rendered_text += html_path.read_text(encoding="utf-8", errors="replace")
            self._record_check(
                "render js loopback content",
                completed.returncode == 0 and "DocPull JS render fixture ready" in rendered_text,
                note="rendered HTML did not include JS-mutated content",
                artifacts=[output],
            )
        finally:
            if old_value is None:
                self.env.pop("DOCPULL_RENDER_ALLOW_LOCAL_TARGETS", None)
            else:
                self.env["DOCPULL_RENDER_ALLOW_LOCAL_TARGETS"] = old_value
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def _mcp(self, pack_dir: Path) -> None:
        script = textwrap.dedent(
            """
            import asyncio
            import os
            from pathlib import Path
            import sys

            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client
            from docpull.surface import PUBLIC_MCP_TOOLS


            async def main():
                python = sys.argv[1]
                pack_dir = sys.argv[2]
                base_dir = Path(sys.argv[3])
                policy_path = sys.argv[4]
                full = sys.argv[5] == "1"
                server = StdioServerParameters(
                    command=python,
                    args=["-m", "docpull", "mcp"],
                    env=os.environ.copy(),
                )
                async with stdio_client(server) as (read, write), ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    names = {tool.name for tool in tools.tools}
                    expected = set(PUBLIC_MCP_TOOLS)
                    assert names == expected, sorted(expected ^ names)

                    async def call(name, args):
                        result = await session.call_tool(name, args)
                        assert not result.isError, f"{name}: {result.content}"
                        return result

                    await call("serve_pack_status", {"pack_dir": pack_dir})
                    if not full:
                        return

                    source_name = "docpull-smoke-example"
                    await call(
                        "add_source",
                        {
                            "name": source_name,
                            "url": "https://example.com",
                            "description": "DocPull MCP smoke source",
                            "category": "user",
                            "max_pages": 1,
                            "force": True,
                        },
                    )
                    try:
                        await call("fetch_url", {"url": "https://example.com", "max_tokens": 100})
                        await call(
                            "render_url",
                            {
                                "url": "https://example.com",
                                "runtime": "local",
                                "output_dir": str(base_dir / "mcp-render"),
                                "allowed_domains": ["example.com"],
                                "timeout_seconds": 30,
                            },
                        )
                        await call("ensure_docs", {"source": source_name, "force": True, "profile": "quick"})
                        await call("list_sources", {"category": "user"})
                        await call("list_indexed", {})
                        await call("grep_docs", {"pattern": "Example", "library": source_name, "limit": 5})
                        docs_root = Path(os.environ["DOCPULL_DOCS_DIR"]) / source_name
                        md_files = sorted(path for path in docs_root.rglob("*.md") if path.is_file())
                        assert md_files, f"ensure_docs did not write markdown under {docs_root}"
                        rel_path = md_files[0].relative_to(docs_root).as_posix()
                        await call("read_doc", {"library": source_name, "path": rel_path, "line_start": 1})

                        await call("pack_score", {"pack_dir": pack_dir})
                        await call("pack_diff", {"old_pack_dir": pack_dir, "new_pack_dir": pack_dir})
                        await call(
                            "refresh_pack",
                            {
                                "pack_dir": pack_dir,
                                "output_dir": str(base_dir / "mcp-refresh"),
                                "dry_run": True,
                            },
                        )
                        await call("audit_pack", {"pack_dir": pack_dir})
                        await call("pack_citations", {"pack_dir": pack_dir})
                        await call("pack_entities", {"pack_dir": pack_dir, "limit": 20})
                        await call("pack_search", {"pack_dir": pack_dir, "query": "HTTP", "limit": 5})
                        await call(
                            "pack_brief",
                            {
                                "pack_dir": pack_dir,
                                "objective": "Summarize package context",
                                "max_excerpts": 3,
                            },
                        )
                        await call(
                            "pack_prepare",
                            {
                                "pack_dir": pack_dir,
                                "objective": "Summarize package context",
                                "search_queries": ["HTTP"],
                                "search_limit": 3,
                                "entity_limit": 20,
                                "graph_entity_limit": 25,
                            },
                        )
                        await call("graph_build", {"pack_dir": pack_dir, "entity_limit": 25})
                        await call("graph_status", {"pack_dir": pack_dir})
                        await call("graph_query", {"pack_dir": pack_dir, "query": "requests", "limit": 5})
                        await call(
                            "graph_neighbors",
                            {"pack_dir": pack_dir, "entity": "requests", "limit": 5},
                        )
                        await call("graph_refresh", {"pack_dir": pack_dir, "entity_limit": 25})
                        await call("validate_policy", {"policy_path": policy_path})
                        await call(
                            "export_pack",
                            {
                                "pack_dir": pack_dir,
                                "format": "openai-vector-jsonl",
                                "output": str(base_dir / "mcp-export" / "openai.jsonl"),
                            },
                        )
                    finally:
                        await call("remove_source", {"name": source_name, "delete_cache": True})


            asyncio.run(main())
            """
        )
        self._run(
            "mcp stdio full" if self.args.full_mcp else "mcp stdio",
            [
                str(self.python),
                "-c",
                script,
                str(self.python),
                str(pack_dir),
                str(self.base),
                str(self.base / "policy.yml"),
                "1" if self.args.full_mcp else "0",
            ],
            cwd=self.repo,
            timeout=360 if self.args.full_mcp else 90,
        )

    def _project_lifecycle(self) -> None:
        project = self.base / "project"
        project.mkdir(parents=True, exist_ok=True)
        self._docpull("project init", ["init", "real-project"], cwd=project)
        self._docpull("sources list", ["sources", "list", "--json"], cwd=project)
        self._docpull(
            "project add url",
            ["add", "https://www.python.org/blogs/", "--name", "python-blogs", "--type", "html"],
            cwd=project,
        )
        self._docpull(
            "project add package",
            ["add", "pypi:requests", "--name", "requests-package", "--type", "package"],
            cwd=project,
        )
        self._bound_project_smoke_crawl(project)
        self._docpull("project install", ["install", "--json"], cwd=project)
        self._docpull("project sync run-a", ["sync", "--run-id", "run_a", "--json"], cwd=project, timeout=300)
        self._docpull("project deps", ["deps", "--json"], cwd=project)
        self._docpull("project status", ["status", "--json"], cwd=project)
        self._docpull("project history", ["history", "--json", "--limit", "5"], cwd=project)
        self._docpull("project review", ["review", "--run", "run_a", "--json"], cwd=project)
        self._docpull("project sync run-b", ["sync", "--run-id", "run_b", "--json"], cwd=project, timeout=300)
        self._docpull("project diff", ["diff", "--from", "run_a", "--to", "run_b", "--json"], cwd=project)
        self._docpull(
            "project release",
            ["release", "context-pack", "--target", "openai", "--run", "run_b", "--tag", "real-smoke"],
            cwd=project,
        )
        self._docpull("project ci", ["ci", ".", "--prepare", "--json"], cwd=project, timeout=180)

    def _bound_project_smoke_crawl(self, project: Path) -> None:
        config_path = project / "docpull.yaml"
        text = config_path.read_text(encoding="utf-8")
        replacements = {
            "max_pages: 500": "max_pages: 3",
            "max_depth: 5": "max_depth: 1",
            "max_concurrent: 10": "max_concurrent: 2",
            "per_host_concurrent: 3": "per_host_concurrent: 1",
            "rate_limit: 0.5": "rate_limit: 0.1",
            "streaming_discovery: true": "streaming_discovery: false",
        }
        for old, new in replacements.items():
            text = text.replace(old, new)
        config_path.write_text(text, encoding="utf-8")

    def _watch(self) -> None:
        watch_dir = self.base / "watch"
        watch_dir.mkdir(parents=True, exist_ok=True)
        self._docpull(
            "watch one-shot",
            [
                "watch",
                "https://www.python.org/blogs/",
                "--export",
                "openai",
                "--max-pages",
                "1",
                "--max-depth",
                "1",
            ],
            cwd=watch_dir,
            timeout=180,
        )

    def _cloud_render_smokes(self) -> None:
        for runtime in ("vercel", "e2b"):
            self._docpull(
                f"render check {runtime}",
                ["render", "--check", "--runtime", runtime],
                timeout=60,
                classification="optional",
                allow_codes={0, 1},
            )
            self._docpull(
                f"render live {runtime}",
                [
                    "render",
                    "https://example.com",
                    "--runtime",
                    runtime,
                    "--live-smoke",
                    "-o",
                    str(self.base / "render" / runtime),
                    "--allowed-domain",
                    "example.com",
                    "--cloud-max-estimated-cost",
                    "0.20",
                    "--quiet",
                ],
                timeout=240,
                classification="optional",
                allow_codes={0, 1},
            )

    def _server(self, name: str, args: list[str]) -> None:
        port = _free_port()
        rendered_args = [str(port) if arg == "{port}" else arg for arg in args]
        cmd = [str(self.python), "-m", "docpull", *rendered_args]
        started = time.time()
        process = subprocess.Popen(  # nosec B603
            cmd,
            cwd=self.repo,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            health = f"http://127.0.0.1:{port}/health"
            last_error = ""
            for _ in range(100):
                if process.poll() is not None:
                    stdout, stderr = process.communicate(timeout=5)
                    self._record(
                        SmokeResult(
                            name=name,
                            status="fail",
                            cmd=cmd,
                            cwd=str(self.repo),
                            code=process.returncode,
                            seconds=round(time.time() - started, 3),
                            stdout_tail=(stdout or "")[-1000:],
                            stderr_tail=(stderr or "")[-1000:],
                            note="server exited before health check",
                        )
                    )
                    return
                try:
                    _url_ok(health)
                    self._record(
                        SmokeResult(
                            name=name,
                            status="pass",
                            cmd=cmd,
                            cwd=str(self.repo),
                            code=0,
                            seconds=round(time.time() - started, 3),
                            artifacts=[health],
                        )
                    )
                    return
                except OSError as err:
                    last_error = str(err)
                    time.sleep(0.2)
            self._record(
                SmokeResult(
                    name=name,
                    status="fail",
                    cmd=cmd,
                    cwd=str(self.repo),
                    seconds=round(time.time() - started, 3),
                    stderr_tail=last_error,
                    note="health check timeout",
                )
            )
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _url_ok(url: str) -> None:
    _require_allowed_smoke_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "docpull-real-feature-smoke/1.0"})
    with urllib.request.urlopen(request, timeout=5) as response:  # nosec B310
        response.read(128)


def _require_allowed_smoke_url(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}:
        return
    raise ValueError(f"Refusing non-HTTPS/non-loopback smoke URL: {url}")


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _record_has_provenance(record: dict[str, Any]) -> bool:
    return bool(record.get("citation_id") or record.get("source_citation_id")) and bool(
        record.get("record_citation_id")
    )


def create_parser() -> argparse.ArgumentParser:
    repo_default = Path(__file__).resolve().parents[1]
    venv_python = repo_default / ".venv" / "bin" / "python"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=repo_default)
    parser.add_argument("--python", default=venv_python if venv_python.exists() else sys.executable)
    parser.add_argument("--base-dir")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument(
        "--quick", action="store_true", help="Run a smaller real-data subset for fast preflights"
    )
    parser.add_argument("--skip-project", action="store_true")
    parser.add_argument("--skip-render", action="store_true")
    parser.add_argument("--full-mcp", action="store_true", help="Call every public MCP tool over stdio")
    parser.add_argument(
        "--strict-ci",
        action="store_true",
        help="Require zero unexpected Context CI warnings on clean release fixtures",
    )
    parser.add_argument(
        "--auth-matrix",
        action="store_true",
        help="Run bearer/basic/cookie/custom-header auth checks against a loopback fixture",
    )
    parser.add_argument(
        "--monitor-soak-minutes",
        type=float,
        default=0.0,
        help="Run a bounded monitor trigger/report soak for this many minutes",
    )
    parser.add_argument(
        "--include-cloud", action="store_true", help="Also attempt Vercel/E2B cloud render smokes"
    )
    parser.add_argument(
        "--trust-render-targets",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Set DOCPULL_RENDER_TRUSTED_BROWSER_TARGETS=1 for the local render lane",
    )
    return parser


def command_plan_for_args(args: argparse.Namespace) -> list[str]:
    names = [
        "download real input files",
        "root fetch markdown/json/ndjson/sqlite/okf",
        "parse",
        "openapi-pack",
        "feed-pack",
        "paper-pack",
        "repo-pack",
        "package-pack",
        "standards-pack",
        "dataset-pack",
        "transcript-pack",
        "wiki-pack",
        "pack validate/score/audit/basis/search/brief/entities/sources/citations/redact/diff",
        "export all public formats including parquet and cursor-rules",
        "graph build/status/query/neighbors/refresh",
        "context ci",
        "serve/share",
        "policy/auth/monitor",
        "local render",
        "mcp stdio",
        "project init/add/install/sync/diff/review/release/ci",
        "watch one-shot",
    ]
    if args.strict_ci:
        names.append("strict Context CI zero-warning fixture")
    if args.auth_matrix:
        names.append("loopback auth matrix bearer/basic/cookie/header")
    if args.full_mcp:
        names.append("full MCP public tool calls")
    if args.monitor_soak_minutes:
        names.append(f"bounded monitor soak ({args.monitor_soak_minutes:g} minutes)")
    if args.include_cloud:
        names.append("cloud render runtimes when credentials/tools are available")
    return names


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    if args.plan_only:
        payload = {
            "schema_version": 1,
            "base_dir": str(Path(args.base_dir).resolve()) if args.base_dir else None,
            "plan": command_plan_for_args(args),
        }
        if args.json_output:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            plan = payload["plan"]
            if not isinstance(plan, list):
                raise TypeError("plan payload must be a list")
            for item in plan:
                print(item)
        return 0
    smoke = RealFeatureSmoke(args)
    payload = smoke.run_all()
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(
            f"real feature smoke: {payload['passed']}/{payload['total']} passed; "
            f"failed_required={payload['failed_required']}; report={payload['report']}"
        )
    return 0 if payload["failed_required"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
