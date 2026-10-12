"""Local blinded human review of encrypted run evidence.

Plaintext outputs stay in memory. Label files hold only case ids, trial indexes,
and grades, in the format ``audit-scorer`` reads.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import secrets
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, cast

from .evidence import evidence_filename
from .integrity import file_sha256, load_portable_report, strict_json_file, strict_json_loads
from .models import BenchmarkSuite, PortableReport, ReportObservation

PASSING_GRADE = 2
SLOTS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_SAFE_SYSTEM = re.compile(r"[^a-zA-Z0-9._-]+")


class ReviewSession:
    """Blinded grading state for matching runs of one suite."""

    def __init__(
        self,
        suite_path: Path,
        report_paths: list[Path],
        *,
        evidence_dir: Path,
        identity: Path,
        labels_dir: Path,
        seed: int | None = None,
    ) -> None:
        if not report_paths:
            raise ValueError("review requires at least one report")
        if len(report_paths) > len(SLOTS):
            raise ValueError(f"review supports at most {len(SLOTS)} reports")
        suite_sha256 = file_sha256(suite_path)
        self.cases = {case.id: case for case in BenchmarkSuite.from_yaml(suite_path).cases}
        self.reports: list[PortableReport] = []
        self.report_sha256: list[str] = []
        for path in report_paths:
            report = load_portable_report(path)
            if report.evidence_status != "integrity-checked-v3":
                raise ValueError(f"{path}: review requires an integrity-checked-v3 report")
            if report.manifest.suite_sha256 != suite_sha256:
                raise ValueError(f"{path}: report was not produced from this suite file")
            self.reports.append(report)
            self.report_sha256.append(file_sha256(path))
        systems = [report.manifest.system for report in self.reports]
        if len(set(systems)) != len(systems):
            raise ValueError("review reports must come from distinct systems")
        self.evidence_dir = evidence_dir
        self.identity = identity
        self.labels_dir = labels_dir
        self._lock = threading.Lock()
        self._observations: list[dict[tuple[str, int], ReportObservation]] = [
            {(item.case_id, item.trial_index): item for item in report.observations}
            for report in self.reports
        ]
        for report, observations in zip(self.reports, self._observations, strict=True):
            if any(item.evidence_ciphertext_sha256 is None for item in observations.values()):
                raise ValueError(
                    f"{report.manifest.system}: report has no encrypted evidence; "
                    "rerun with --evidence-dir and --evidence-recipient"
                )
        self.items = sorted({key for observations in self._observations for key in observations})
        rng = random.Random(secrets.randbits(64) if seed is None else seed)
        self._slot_reports: list[list[int]] = []
        for key in self.items:
            present = [index for index, observations in enumerate(self._observations) if key in observations]
            rng.shuffle(present)
            self._slot_reports.append(present)
        self._grades = [self._load_labels(index) for index in range(len(self.reports))]

    def overview(self) -> list[dict[str, Any]]:
        return [
            {
                "index": index,
                "case_id": case_id,
                "trial_index": trial_index,
                "slots": len(self._slot_reports[index]),
                "graded": sum(
                    (case_id, trial_index) in self._grades[report] for report in self._slot_reports[index]
                ),
            }
            for index, (case_id, trial_index) in enumerate(self.items)
        ]

    def item(self, index: int) -> dict[str, Any]:
        case_id, trial_index = self.items[index]
        case = self.cases.get(case_id)
        slots = []
        for slot, report in zip(SLOTS, self._slot_reports[index], strict=False):
            output = self._decrypt(report, (case_id, trial_index))
            slots.append(
                {
                    "slot": slot,
                    "status": output.get("status"),
                    "payload": output.get("payload"),
                    "grade": self._grades[report].get((case_id, trial_index)),
                }
            )
        return {
            "index": index,
            "case_id": case_id,
            "trial_index": trial_index,
            "input": case.input.model_dump(mode="json", exclude={"case_id"}) if case else None,
            "slots": slots,
        }

    def grade(self, index: int, slot: str, grade: int) -> None:
        if isinstance(grade, bool) or not isinstance(grade, int) or not 0 <= grade <= 3:
            raise ValueError("grade must be an integer from 0 to 3")
        if not 0 <= index < len(self.items):
            raise ValueError("unknown review item")
        slots = self._slot_reports[index]
        position = SLOTS.find(slot) if len(slot) == 1 else -1
        if not 0 <= position < len(slots):
            raise ValueError("unknown review slot")
        report = slots[position]
        with self._lock:
            self._grades[report][self.items[index]] = grade
            self._write_labels(report)

    def _labels_path(self, report: int) -> Path:
        system = _SAFE_SYSTEM.sub("-", self.reports[report].manifest.system).strip("-") or "system"
        return self.labels_dir / f"{system}.labels.json"

    def _load_labels(self, report: int) -> dict[tuple[str, int], int]:
        path = self._labels_path(report)
        if not path.exists():
            return {}
        payload = strict_json_file(path)
        if payload.get("report_sha256") != self.report_sha256[report]:
            raise ValueError(f"{path}: labels belong to a different report")
        grades: dict[tuple[str, int], int] = {}
        for item in payload.get("labels", []):
            key = (str(item["case_id"]), int(item["trial_index"]))
            if key not in self._observations[report]:
                raise ValueError(f"{path}: label has no report observation: {key[0]} trial {key[1]}")
            grades[key] = int(item["grade"])
        return grades

    def _write_labels(self, report: int) -> None:
        manifest = self.reports[report].manifest
        payload = {
            "schema_version": 1,
            "system": manifest.system,
            "run_id": manifest.run_id,
            "report_sha256": self.report_sha256[report],
            "passing_grade": PASSING_GRADE,
            "labels": [
                {
                    "case_id": case_id,
                    "trial_index": trial_index,
                    "grade": grade,
                    "passed": grade >= PASSING_GRADE,
                }
                for (case_id, trial_index), grade in sorted(self._grades[report].items())
            ],
        }
        path = self._labels_path(report)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(json.dumps(payload, indent=2) + "\n")
            Path(temporary).replace(path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _decrypt(self, report: int, key: tuple[str, int]) -> dict[str, Any]:
        observation = self._observations[report][key]
        path = self.evidence_dir / self.reports[report].manifest.run_id / evidence_filename(*key)
        if file_sha256(path) != observation.evidence_ciphertext_sha256:
            raise ValueError(f"{path.name}: ciphertext does not match the report")
        process = subprocess.run(
            ["age", "--decrypt", "--identity", str(self.identity), str(path)],
            capture_output=True,
            check=False,
            timeout=30,
        )
        if process.returncode != 0:
            raise ValueError(f"{path.name}: age decryption failed")
        if hashlib.sha256(process.stdout).hexdigest() != observation.normalized_output_sha256:
            raise ValueError(f"{path.name}: decrypted output does not match the report commitment")
        payload = strict_json_loads(process.stdout)
        if not isinstance(payload, dict):
            raise ValueError(f"{path.name}: decrypted output must be an object")
        return payload


def serve_review(session: ReviewSession, *, port: int = 0) -> None:
    """Serve the review page on loopback until interrupted."""
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(session))
    print(f"review: http://127.0.0.1:{server.server_port}/  (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _handler(session: ReviewSession) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: Any) -> None:
            return

        def _local(self) -> bool:
            # Rejects DNS-rebinding pages that would otherwise read decrypted output.
            port = cast(ThreadingHTTPServer, self.server).server_port
            return self.headers.get("Host") in {f"127.0.0.1:{port}", f"localhost:{port}"}

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, payload: Any) -> None:
            self._send(status, json.dumps(payload).encode(), "application/json")

        def do_GET(self) -> None:  # noqa: N802
            if not self._local():
                return self._json(403, {"error": "forbidden host"})
            try:
                if self.path == "/":
                    page = files(__package__).joinpath("review.html").read_bytes()
                    return self._send(200, page, "text/html; charset=utf-8")
                if self.path == "/api/items":
                    return self._json(200, session.overview())
                if self.path.startswith("/api/items/"):
                    index = int(self.path.removeprefix("/api/items/"))
                    if not 0 <= index < len(session.items):
                        return self._json(404, {"error": "unknown review item"})
                    return self._json(200, session.item(index))
            except ValueError as error:
                return self._json(400, {"error": str(error)})
            return self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if not self._local() or self.headers.get("Content-Type") != "application/json":
                return self._json(403, {"error": "forbidden request"})
            if self.path != "/api/grades":
                return self._json(404, {"error": "not found"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = strict_json_loads(self.rfile.read(min(length, 4096)))
                session.grade(int(payload["index"]), str(payload["slot"]), payload["grade"])
            except (KeyError, TypeError, ValueError) as error:
                return self._json(400, {"error": str(error)})
            return self._json(200, {"saved": True})

    return Handler
