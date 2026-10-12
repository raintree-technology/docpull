from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from docpull_bench.adapters import ReplayAdapter
from docpull_bench.audit import audit_scorer
from docpull_bench.review import ReviewSession, _handler
from docpull_bench.runner import run_suite

ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "cases" / "controlled-v2.yaml"
CASE = "extract.fixture.01"

pytestmark = pytest.mark.skipif(shutil.which("age-keygen") is None, reason="age is not installed")


def _runs(tmp_path: Path) -> tuple[list[Path], Path, Path]:
    identity = tmp_path / "key.txt"
    subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
    recipient = subprocess.run(
        ["age-keygen", "-y", str(identity)], check=True, capture_output=True, text=True
    ).stdout.strip()
    evidence = tmp_path / "evidence"
    reports = []
    for system in ("alpha", "beta"):
        _report, run_dir = run_suite(
            SUITE,
            ReplayAdapter(system=system, version="2", replay_dir=ROOT / "replays" / "controlled-v2"),
            output_dir=tmp_path / "runs",
            case_ids={CASE},
            progress=False,
            evidence_dir=evidence,
            evidence_recipient=recipient,
        )
        reports.append(run_dir / "report.json")
    return reports, evidence, identity


def test_review_grades_blinded_outputs_into_audit_labels(tmp_path: Path) -> None:
    reports, evidence, identity = _runs(tmp_path)
    labels = tmp_path / "labels"
    session = ReviewSession(
        SUITE, reports, evidence_dir=evidence, identity=identity, labels_dir=labels, seed=1
    )

    item = session.item(0)
    assert item["case_id"] == CASE
    assert [slot["slot"] for slot in item["slots"]] == ["A", "B"]
    assert "alpha" not in json.dumps(item) and "beta" not in json.dumps(item)
    assert item["slots"][0]["payload"]["records"]

    session.grade(0, "A", 3)
    session.grade(0, "B", 1)
    with pytest.raises(ValueError, match="grade"):
        session.grade(0, "A", 4)

    passed = {}
    for path in sorted(labels.glob("*.labels.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert "content" not in path.read_text(encoding="utf-8")
        passed[payload["system"]] = payload["labels"][0]["passed"]
        report = reports[0] if payload["system"] == "alpha" else reports[1]
        assert audit_scorer(report, path)["sample_size"] == 1
    assert sorted(passed.values()) == [False, True]

    resumed = ReviewSession(SUITE, reports, evidence_dir=evidence, identity=identity, labels_dir=labels)
    assert resumed.overview()[0]["graded"] == 2


def test_review_rejects_tampered_evidence(tmp_path: Path) -> None:
    reports, evidence, identity = _runs(tmp_path)
    session = ReviewSession(
        SUITE, reports, evidence_dir=evidence, identity=identity, labels_dir=tmp_path / "labels"
    )
    for ciphertext in evidence.rglob("*.age"):
        ciphertext.chmod(0o600)
        ciphertext.write_bytes(ciphertext.read_bytes() + b"x")
    with pytest.raises(ValueError, match="ciphertext"):
        session.item(0)


def test_review_server_rejects_foreign_hosts_and_form_posts(tmp_path: Path) -> None:
    reports, evidence, identity = _runs(tmp_path)
    session = ReviewSession(
        SUITE, reports, evidence_dir=evidence, identity=identity, labels_dir=tmp_path / "labels"
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(session))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:

        def request(method: str, path: str, headers: dict[str, str], body: str | None = None) -> int:
            connection = http.client.HTTPConnection("127.0.0.1", port)
            connection.request(method, path, body=body, headers=headers)
            status = connection.getresponse().status
            connection.close()
            return status

        local = {"Host": f"127.0.0.1:{port}"}
        assert request("GET", "/api/items/0", local) == 200
        assert request("GET", "/api/items/0", {"Host": f"evil.example:{port}"}) == 403
        grade = json.dumps({"index": 0, "slot": "A", "grade": 2})
        form = {**local, "Content-Type": "text/plain"}
        assert request("POST", "/api/grades", form, grade) == 403
        assert request("POST", "/api/grades", {**local, "Content-Type": "application/json"}, grade) == 200
    finally:
        server.shutdown()
        server.server_close()
