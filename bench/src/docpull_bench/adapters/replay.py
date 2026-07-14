"""Replay normalized schema-v2 observations without network or paid calls."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..models import BenchmarkInput, Lane, RunObservation
from .base import AdapterError

MAX_REPLAY_BYTES = 32 * 1024 * 1024


class ReplayAdapter:
    capabilities = frozenset(Lane)
    cache_policy = "replay"
    retry_policy = "no_retries"
    pricing_snapshot: str | None = None

    def __init__(self, *, system: str, version: str, replay_dir: Path) -> None:
        self.system = system
        self.version = version
        self.replay_dir = replay_dir

    def preflight(self, inputs: list[BenchmarkInput], *, repeat: int) -> None:
        del repeat
        oversized = [
            item.case_id
            for item in inputs
            if (path := self.replay_dir / f"{item.case_id}.json").exists()
            and path.stat().st_size > MAX_REPLAY_BYTES
        ]
        if oversized:
            raise AdapterError(f"oversized replay observations: {', '.join(oversized)}")

    def public_config(self) -> dict[str, Any]:
        manifest = sorted(
            (path.name, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in self.replay_dir.glob("*.json")
            if path.is_file()
        )
        return {
            "system": self.system,
            "version": self.version,
            "capabilities": sorted(lane.value for lane in self.capabilities),
            "replay_manifest_sha256": hashlib.sha256(json.dumps(manifest).encode()).hexdigest(),
        }

    def run(self, inputs: BenchmarkInput, output_root: Path) -> RunObservation:
        del output_root
        path = self.replay_dir / f"{inputs.case_id}.json"
        if not path.exists():
            raise AdapterError(f"missing replay observation: {path}")
        if path.stat().st_size > MAX_REPLAY_BYTES:
            raise AdapterError(f"replay observation exceeds {MAX_REPLAY_BYTES} bytes")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(
            {
                "schema_version": 2,
                "case_id": inputs.case_id,
                "system": self.system,
                "adapter_version": self.version,
            }
        )
        return RunObservation.model_validate(payload)
