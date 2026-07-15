"""Black-box DocPull adapter using only the installed public CLI."""

from __future__ import annotations

import importlib.metadata
import json
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from ..lifecycle import run_lifecycle_case
from ..models import (
    ArtifactRecord,
    BenchmarkInput,
    ChangeEvent,
    ChangeInput,
    ChangePayload,
    CheckPayload,
    ContentPayload,
    CrawlInput,
    ExtractInput,
    Lane,
    LifecycleInput,
    PackInput,
    PackPayload,
    ParseInput,
    PolicyInput,
    RankedResult,
    RetrievalInput,
    RetrievalPayload,
    RunObservation,
)
from ..sanitization import scrub_secrets

MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
REPO_ROOT = Path(__file__).resolve().parents[4]


class DocPullAdapter:
    """Exercise the user-visible DocPull surface without private module imports."""

    system = "docpull"
    try:
        version = importlib.metadata.version("docpull")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    capabilities = frozenset(
        {
            Lane.EXTRACT,
            Lane.CRAWL,
            Lane.PARSE,
            Lane.PACK,
            Lane.LIFECYCLE,
            Lane.CHANGE,
            Lane.RETRIEVAL,
            Lane.POLICY,
        }
    )
    cache_policy = "disabled"
    retry_policy = "docpull_public_defaults"
    pricing_snapshot: str | None = None

    def __init__(self) -> None:
        self._fixture_lock = threading.Lock()
        self._retrieval_packs: dict[Path, Path] = {}

    def preflight(self, inputs: list[BenchmarkInput], *, repeat: int) -> None:
        del repeat
        for item in inputs:
            for value in _input_paths(item):
                path = _repo_path(value)
                if not path.exists():
                    raise ValueError(f"benchmark input path does not exist: {value}")

    def public_config(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "version": self.version,
            "capabilities": sorted(lane.value for lane in self.capabilities),
            "cache_policy": self.cache_policy,
            "retry_policy": self.retry_policy,
            "paid_routes": False,
            "browser": False,
            "remote_documents": "pdf",
            "remote_document_backend": "auto",
        }

    def run(self, inputs: BenchmarkInput, output_root: Path) -> RunObservation:
        if inputs.lane not in self.capabilities:
            return _unsupported(self, inputs)
        case_dir = output_root / inputs.case_id / uuid.uuid4().hex
        case_dir.mkdir(parents=True, exist_ok=False)
        if isinstance(inputs, (ExtractInput, CrawlInput)):
            return self._web(inputs, case_dir, output_root)
        if isinstance(inputs, ParseInput):
            return self._parse(inputs, case_dir, output_root)
        if isinstance(inputs, PackInput):
            return self._pack(inputs, case_dir, output_root)
        if isinstance(inputs, LifecycleInput):
            return self._lifecycle(inputs, case_dir)
        if isinstance(inputs, ChangeInput):
            return self._change(inputs, case_dir, output_root)
        if isinstance(inputs, RetrievalInput):
            return self._retrieval(inputs, case_dir, output_root)
        if isinstance(inputs, PolicyInput):
            return self._policy(inputs, case_dir)
        return _unsupported(self, inputs)

    def _web(
        self,
        inputs: ExtractInput | CrawlInput,
        case_dir: Path,
        output_root: Path,
    ) -> RunObservation:
        command = [
            sys.executable,
            "-m",
            "docpull",
            inputs.url,
            "--format",
            "ndjson",
            "--output-dir",
            str(case_dir),
            "--max-pages",
            str(1 if isinstance(inputs, ExtractInput) else inputs.max_pages),
            "--max-depth",
            str(1 if isinstance(inputs, ExtractInput) else max(inputs.max_depth, 1)),
            "--rate-limit",
            "0",
            "--budget",
            "0",
            "--quiet",
            "--remote-documents",
            "pdf",
        ]
        if isinstance(inputs, ExtractInput):
            command.append("--single")
        else:
            if inputs.include_path_prefixes:
                command.extend(
                    ["--include-paths", *[_path_glob(value) for value in inputs.include_path_prefixes]]
                )
            if inputs.exclude_path_prefixes:
                command.extend(
                    ["--exclude-paths", *[_path_glob(value) for value in inputs.exclude_path_prefixes]]
                )
        process, elapsed = _run(command, timeout=inputs.timeout_seconds)
        records = _read_records(case_dir / "documents.ndjson")
        return _content_observation(
            self,
            inputs,
            process,
            elapsed,
            records,
            case_dir,
            output_root,
        )

    def _parse(self, inputs: ParseInput, case_dir: Path, output_root: Path) -> RunObservation:
        backend = "text" if inputs.backend == "builtin" else inputs.backend
        command = [
            sys.executable,
            "-m",
            "docpull",
            "parse",
            str(_repo_path(inputs.path)),
            "--output-dir",
            str(case_dir),
            "--backend",
            backend,
            "--format",
            "json",
        ]
        process, elapsed = _run(command, timeout=inputs.timeout_seconds)
        if process.returncode and "ocr" in (process.stderr or process.stdout).casefold():
            return RunObservation(
                case_id=inputs.case_id,
                system=self.system,
                status="unsupported",
                elapsed_seconds=elapsed,
                cost_usd=0,
                cost_kind="actual",
                cost_basis="No OCR, paid provider, or cloud route was enabled.",
                request_count=0,
                adapter_version=self.version,
                error=_bounded_error(process.stderr or process.stdout),
                artifacts=_artifacts(case_dir, output_root),
            )
        records = _read_records(case_dir / "documents.ndjson")
        return _content_observation(
            self,
            inputs,
            process,
            elapsed,
            records,
            case_dir,
            output_root,
        )

    def _pack(self, inputs: PackInput, case_dir: Path, output_root: Path) -> RunObservation:
        source = _repo_path(inputs.path)
        pack = case_dir / "pack"
        shutil.copytree(source, pack)
        elapsed = 0.0
        process: subprocess.CompletedProcess[str] | None = None
        fixture_records = pack / "records.ndjson"
        if fixture_records.exists() and not (pack / "documents.ndjson").exists():
            process, setup_elapsed = _run(
                [
                    sys.executable,
                    "-m",
                    "docpull",
                    "parse",
                    str(fixture_records),
                    "--output-dir",
                    str(pack),
                    "--backend",
                    "text",
                    "--no-chunks",
                    "--format",
                    "json",
                ],
                timeout=inputs.timeout_seconds,
            )
            elapsed += setup_elapsed
        if (
            (process is None or process.returncode == 0)
            and inputs.action == "validate"
            and inputs.contract_level in {"agent", "eval"}
        ):
            prepare = [sys.executable, "-m", "docpull", "pack", "prepare", str(pack)]
            if inputs.contract_level == "eval":
                prepare.append("--eval-grade")
            process, prepare_elapsed = _run(prepare, timeout=inputs.timeout_seconds)
            elapsed += prepare_elapsed
        if inputs.action == "validate":
            output = case_dir / "validation.json"
            command = [
                sys.executable,
                "-m",
                "docpull",
                "pack",
                "validate",
                str(pack),
                "--level",
                inputs.contract_level,
                "--format",
                "json",
                "--output",
                str(output),
            ]
        elif inputs.action == "prepare":
            command = [sys.executable, "-m", "docpull", "pack", "prepare", str(pack)]
            if inputs.contract_level == "eval":
                command.append("--eval-grade")
        else:
            destination = case_dir / "export"
            command = [
                sys.executable,
                "-m",
                "docpull",
                "export",
                str(pack),
                "--format",
                inputs.export_format or "openai-vector-jsonl",
                "--output",
                str(destination),
            ]
        if process is None or process.returncode == 0:
            process, action_elapsed = _run(command, timeout=inputs.timeout_seconds)
            elapsed += action_elapsed
        records = _read_records(pack / "documents.ndjson")
        files = sorted(str(path.relative_to(pack)) for path in pack.rglob("*") if path.is_file())
        identities = [
            str(record.metadata.get("document_id") or record.metadata.get("content_hash") or record.url)
            for record in records
        ]
        status, error = _process_status(process, bool(records))
        return RunObservation(
            case_id=inputs.case_id,
            system=self.system,
            status=status,
            payload=PackPayload(
                records=records,
                files=files,
                contract_level=inputs.contract_level if status == "completed" else None,
                stable_identities=identities,
            )
            if status == "completed"
            else None,
            elapsed_seconds=elapsed,
            cost_usd=0,
            cost_kind="actual",
            cost_basis="No paid provider or cloud route was enabled.",
            request_count=0,
            adapter_version=self.version,
            error=error,
            artifacts=_artifacts(case_dir, output_root),
        )

    def _lifecycle(self, inputs: LifecycleInput, case_dir: Path) -> RunObservation:
        started = time.perf_counter()
        try:
            details = run_lifecycle_case(inputs.check, work_dir=case_dir / "work")
        except Exception as error:  # noqa: BLE001 - benchmark outcome
            return RunObservation(
                case_id=inputs.case_id,
                system=self.system,
                status="failed",
                elapsed_seconds=time.perf_counter() - started,
                cost_usd=0,
                cost_kind="actual",
                request_count=0,
                adapter_version=self.version,
                error=scrub_secrets(f"{type(error).__name__}: {error}"),
            )
        return RunObservation(
            case_id=inputs.case_id,
            system=self.system,
            status="completed",
            payload=CheckPayload(
                details={
                    key: ",".join(value) if isinstance(value, list) else value
                    for key, value in details.items()
                }
            ),
            elapsed_seconds=time.perf_counter() - started,
            cost_usd=0,
            cost_kind="actual",
            cost_basis="Network-free public CLI lifecycle check.",
            request_count=0,
            adapter_version=self.version,
        )

    def _change(self, inputs: ChangeInput, case_dir: Path, output_root: Path) -> RunObservation:
        if inputs.mode != "pack_diff":
            return _unsupported(self, inputs)
        output = case_dir / "diff.json"
        command = [
            sys.executable,
            "-m",
            "docpull",
            "pack",
            "diff",
            str(_repo_path(inputs.before_path)),
            str(_repo_path(inputs.after_path)),
            "--output",
            str(output),
        ]
        process, elapsed = _run(command, timeout=inputs.timeout_seconds)
        payload = _read_json(output) if output.exists() else {}
        events: list[ChangeEvent] = []
        event_fields: tuple[tuple[str, Literal["added", "removed", "changed", "unchanged"]], ...] = (
            ("added_urls", "added"),
            ("removed_urls", "removed"),
            ("changed_urls", "changed"),
            ("unchanged_urls", "unchanged"),
        )
        for key, kind in event_fields:
            events.extend(ChangeEvent(identity=str(value), kind=kind) for value in payload.get(key, []))
        status, error = _process_status(process, output.exists())
        return RunObservation(
            case_id=inputs.case_id,
            system=self.system,
            status=status,
            payload=ChangePayload(events=events) if status == "completed" else None,
            elapsed_seconds=elapsed,
            cost_usd=0,
            cost_kind="actual",
            request_count=0,
            adapter_version=self.version,
            error=error,
            artifacts=_artifacts(case_dir, output_root),
        )

    def _retrieval(self, inputs: RetrievalInput, case_dir: Path, output_root: Path) -> RunObservation:
        pack, setup_error = self._prepare_retrieval_pack(inputs, output_root)
        if setup_error is not None:
            return RunObservation(
                case_id=inputs.case_id,
                system=self.system,
                status="failed",
                elapsed_seconds=0,
                cost_usd=0,
                cost_kind="actual",
                cost_basis="Local fixture preparation failed before the measured query.",
                request_count=0,
                adapter_version=self.version,
                error=_bounded_error(setup_error.stderr or setup_error.stdout),
            )
        output = case_dir / "search.json"
        command = [
            sys.executable,
            "-m",
            "docpull",
            "pack",
            "search",
            str(pack),
            inputs.query,
            "--limit",
            str(inputs.max_results),
            "--output",
            str(output),
        ]
        process, elapsed = _run(command, timeout=inputs.timeout_seconds)
        payload = _read_json(output) if output.exists() else {}
        results = [
            RankedResult(
                identity=_retrieval_identity(item),
                url=str(item.get("url") or "") or None,
                title=str(item.get("title") or ""),
                excerpt=str(item.get("excerpt") or ""),
                score=float(item["score"]) if isinstance(item.get("score"), int | float) else None,
            )
            for item in payload.get("results", [])
            if isinstance(item, dict)
        ]
        status, error = _process_status(process, output.exists())
        index = pack / ".docpull" / "index.sqlite"
        return RunObservation(
            case_id=inputs.case_id,
            system=self.system,
            status=status,
            payload=RetrievalPayload(
                results=results,
                index_bytes=index.stat().st_size if index.exists() else None,
            )
            if status == "completed"
            else None,
            elapsed_seconds=elapsed,
            cost_usd=0,
            cost_kind="actual",
            request_count=0,
            adapter_version=self.version,
            error=error,
            artifacts=_artifacts(case_dir, output_root),
        )

    def _prepare_retrieval_pack(
        self,
        inputs: RetrievalInput,
        output_root: Path,
    ) -> tuple[Path, subprocess.CompletedProcess[str] | None]:
        source = _repo_path(inputs.pack_path)
        with self._fixture_lock:
            existing = self._retrieval_packs.get(output_root)
            if existing is not None and (existing / "documents.ndjson").exists():
                return existing, None
            pack = output_root / "_context_retrieval_pack"
            documents = sorted(path for path in source.iterdir() if path.is_file())
            command = [
                sys.executable,
                "-m",
                "docpull",
                "parse",
                *[str(path) for path in documents],
                "--output-dir",
                str(pack),
                "--backend",
                "text",
                "--no-chunks",
                "--format",
                "json",
            ]
            process, _elapsed = _run(command, timeout=inputs.timeout_seconds)
            if process.returncode != 0 or not (pack / "documents.ndjson").exists():
                return pack, process
            self._retrieval_packs[output_root] = pack
            return pack, None

    def _policy(self, inputs: PolicyInput, case_dir: Path) -> RunObservation:
        if inputs.scenario == "credential_leak":
            lifecycle = LifecycleInput(
                case_id=inputs.case_id, lane=Lane.LIFECYCLE, check="credential_non_persistence"
            )
            return self._lifecycle(lifecycle, case_dir)
        if inputs.scenario == "zero_budget":
            command = [
                sys.executable,
                "-m",
                "docpull",
                "render",
                inputs.target_url or "https://example.com",
                "--runtime",
                "e2b",
                "--live-smoke",
                "--budget",
                "0",
                "--output-dir",
                str(case_dir / "render"),
            ]
        elif inputs.scenario == "private_target":
            command = [
                sys.executable,
                "-m",
                "docpull",
                inputs.target_url or "https://127.0.0.1/private",
                "--single",
                "--budget",
                "0",
                "--output-dir",
                str(case_dir / "private"),
            ]
        elif inputs.scenario == "malformed_config" and inputs.fixture_path:
            command = [
                sys.executable,
                "-m",
                "docpull",
                "policy",
                "validate",
                str(_repo_path(inputs.fixture_path)),
            ]
        else:
            return _unsupported(self, inputs)
        process, elapsed = _run(command, timeout=inputs.timeout_seconds)
        # Policy cases intentionally preserve the public CLI failure as a scored observation.
        return RunObservation(
            case_id=inputs.case_id,
            system=self.system,
            status="failed" if process.returncode else "completed",
            payload=CheckPayload(details={"returncode": process.returncode})
            if process.returncode == 0
            else None,
            elapsed_seconds=elapsed,
            cost_usd=0,
            cost_kind="actual",
            cost_basis="No paid request completed.",
            request_count=0,
            adapter_version=self.version,
            error=_bounded_error(process.stderr or process.stdout) if process.returncode else None,
        )


def _input_paths(inputs: BenchmarkInput) -> list[str]:
    if isinstance(inputs, ParseInput):
        return [inputs.path]
    if isinstance(inputs, PackInput):
        return [inputs.path]
    if isinstance(inputs, ChangeInput):
        return [inputs.before_path, inputs.after_path]
    if isinstance(inputs, RetrievalInput):
        return [inputs.pack_path]
    if isinstance(inputs, PolicyInput) and inputs.fixture_path:
        return [inputs.fixture_path]
    return []


def _repo_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def _run(command: list[str], *, timeout: float) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.perf_counter()
    try:
        process = subprocess.run(
            command,
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        process = subprocess.CompletedProcess(
            command,
            124,
            _timeout_text(error.stdout),
            _timeout_text(error.stderr) or "timeout",
        )
    return process, time.perf_counter() - started


def _timeout_text(value: bytes | str | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _content_observation(
    adapter: DocPullAdapter,
    inputs: BenchmarkInput,
    process: subprocess.CompletedProcess[str],
    elapsed: float,
    records: list[ArtifactRecord],
    case_dir: Path,
    output_root: Path,
) -> RunObservation:
    status, error = _process_status(process, bool(records))
    return RunObservation(
        case_id=inputs.case_id,
        system=adapter.system,
        status=status,
        payload=ContentPayload(records=records, selected_urls=[record.url for record in records])
        if status == "completed"
        else None,
        elapsed_seconds=elapsed,
        cost_usd=0,
        cost_kind="actual",
        cost_basis="No paid provider or cloud route was enabled.",
        request_count=0,
        adapter_version=adapter.version,
        error=error,
        artifacts=_artifacts(case_dir, output_root),
    )


def _process_status(
    process: subprocess.CompletedProcess[str],
    has_output: bool,
) -> tuple[Literal["completed", "failed"], str | None]:
    if process.returncode != 0:
        return "failed", _bounded_error(process.stderr or process.stdout)
    if not has_output:
        return "failed", "DocPull completed without readable benchmark artifacts."
    return "completed", None


def _read_records(path: Path) -> list[ArtifactRecord]:
    if not path.exists() or path.stat().st_size > MAX_ARTIFACT_BYTES:
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload: dict[str, Any] = json.loads(line)
        metadata = {key: value for key, value in payload.items() if key not in {"url", "title", "content"}}
        records.append(
            ArtifactRecord(
                url=str(payload.get("url") or ""),
                title=str(payload.get("title") or ""),
                content=str(payload.get("content") or ""),
                metadata=metadata,
            )
        )
    return records


def _read_json(path: Path) -> dict[str, Any]:
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError(f"benchmark artifact exceeds {MAX_ARTIFACT_BYTES} bytes: {path.name}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _retrieval_identity(item: dict[str, Any]) -> str:
    url = str(item.get("url") or "")
    parsed = urlparse(url)
    if parsed.scheme == "file":
        stem = Path(unquote(parsed.path)).stem
        if stem:
            return stem
    return str(item.get("record_citation_id") or item.get("citation_id") or url)


def _artifacts(case_dir: Path, output_root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(case_dir)): str(path.relative_to(output_root.parent))
        for path in case_dir.rglob("*")
        if path.is_file() and path.stat().st_size <= MAX_ARTIFACT_BYTES
    }


def _path_glob(value: str) -> str:
    return value if value.endswith("*") else f"{value}*"


def _bounded_error(value: str, limit: int = 4000) -> str:
    text = scrub_secrets(value, limit=limit)
    return text[-limit:] if text else "DocPull exited with a nonzero status."


def _unsupported(adapter: DocPullAdapter, inputs: BenchmarkInput) -> RunObservation:
    return RunObservation(
        case_id=inputs.case_id,
        system=adapter.system,
        status="unsupported",
        elapsed_seconds=0,
        cost_usd=0,
        cost_kind="actual",
        cost_basis="No route was executed for an unsupported public capability.",
        request_count=0,
        attempt_count=0,
        adapter_version=adapter.version,
        error=f"DocPull public CLI does not claim the {inputs.lane.value} lane/configuration.",
    )
