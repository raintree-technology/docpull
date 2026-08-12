"""Human-label agreement audit for the deterministic benchmark scorer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .integrity import load_portable_report


def audit_scorer(report_path: Path, labels_path: Path) -> dict[str, Any]:
    """Compare blinded human pass/fail labels with scorer decisions."""
    report = load_portable_report(report_path)
    payload = json.loads(labels_path.read_text(encoding="utf-8"))
    labels = payload.get("labels") if isinstance(payload, dict) else None
    if not isinstance(labels, list) or not labels:
        raise ValueError("scorer audit labels must contain a non-empty labels array")
    machine = {(score.case_id, score.trial_index): score.passed for score in report.scores}
    pairs: list[tuple[bool, bool]] = []
    seen: set[tuple[str, int]] = set()
    for item in labels:
        if not isinstance(item, dict):
            raise ValueError("each scorer audit label must be an object")
        key = (str(item.get("case_id") or ""), int(item.get("trial_index", 1)))
        if key in seen:
            raise ValueError(f"duplicate scorer audit label: {key[0]} trial {key[1]}")
        if key not in machine:
            raise ValueError(f"scorer audit label has no report score: {key[0]} trial {key[1]}")
        if not isinstance(item.get("passed"), bool):
            raise ValueError("scorer audit passed labels must be booleans")
        seen.add(key)
        pairs.append((machine[key], item["passed"]))
    agreement = sum(machine_pass == human_pass for machine_pass, human_pass in pairs) / len(pairs)
    machine_rate = sum(machine_pass for machine_pass, _ in pairs) / len(pairs)
    human_rate = sum(human_pass for _, human_pass in pairs) / len(pairs)
    chance = machine_rate * human_rate + (1 - machine_rate) * (1 - human_rate)
    kappa = (agreement - chance) / (1 - chance) if chance < 1 else 1.0
    return {
        "schema_version": 1,
        "scorer_version": report.manifest.scorer_version,
        "sample_size": len(pairs),
        "agreement": agreement,
        "cohen_kappa": kappa,
        "machine_pass_rate": machine_rate,
        "human_pass_rate": human_rate,
        "disagreements": sum(machine_pass != human_pass for machine_pass, human_pass in pairs),
    }
