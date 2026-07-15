"""Explicit command adapter with a minimal environment and bounded output."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from ..models import BenchmarkInput, Lane, RunObservation
from ..sanitization import scrub_secrets
from .base import AdapterError

MAX_COMMAND_OUTPUT_BYTES = 32 * 1024 * 1024
BASE_ENV_NAMES = frozenset({"HOME", "LANG", "LC_ALL", "PATH", "SYSTEMROOT", "TEMP", "TMP", "TMPDIR"})


class CommandAdapter:
    capabilities = frozenset(Lane)
    cache_policy = "adapter_declared"
    retry_policy = "max_attempts=1"
    pricing_snapshot: str | None = None

    def __init__(
        self,
        *,
        system: str,
        version: str,
        command: str,
        allowed_env: list[str] | None = None,
    ) -> None:
        if "{input}" not in command or "{output}" not in command:
            raise AdapterError("command must contain {input} and {output} placeholders")
        self.system = system
        self.version = version
        self.command = command
        self.allowed_env = sorted(set(allowed_env or []))

    def preflight(self, inputs: list[BenchmarkInput], *, repeat: int) -> None:
        del inputs, repeat
        missing = [name for name in self.allowed_env if name not in os.environ]
        if missing:
            raise AdapterError(
                f"command adapter allowlisted environment values are missing: {', '.join(missing)}"
            )

    def public_config(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "version": self.version,
            "capabilities": sorted(lane.value for lane in self.capabilities),
            "command_sha256": hashlib.sha256(self.command.encode()).hexdigest(),
            "allowed_env_names": self.allowed_env,
        }

    def run(self, inputs: BenchmarkInput, output_root: Path) -> RunObservation:
        case_dir = output_root / inputs.case_id / uuid.uuid4().hex
        case_dir.mkdir(parents=True, exist_ok=False)
        input_path = case_dir / "adapter.input.json"
        output_path = case_dir / "adapter.output.json"
        input_path.write_text(inputs.model_dump_json(indent=2), encoding="utf-8")
        argv = [
            token.format(input=str(input_path), output=str(output_path))
            for token in shlex.split(self.command)
        ]
        env = {name: value for name, value in os.environ.items() if name in BASE_ENV_NAMES}
        env.update({name: os.environ[name] for name in self.allowed_env})
        env["DOCPULL_BENCH_CASE_ID"] = inputs.case_id

        started = time.perf_counter()
        try:
            process = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                timeout=inputs.timeout_seconds,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return RunObservation(
                case_id=inputs.case_id,
                system=self.system,
                status="failed",
                elapsed_seconds=time.perf_counter() - started,
                adapter_version=self.version,
                error=f"Adapter timed out after {inputs.timeout_seconds:g} seconds.",
            )

        elapsed = time.perf_counter() - started
        if process.returncode != 0:
            error = scrub_secrets(process.stderr or process.stdout)
            return RunObservation(
                case_id=inputs.case_id,
                system=self.system,
                status="failed",
                elapsed_seconds=elapsed,
                adapter_version=self.version,
                error=error or f"Adapter exited with status {process.returncode}.",
            )
        if not output_path.exists():
            raise AdapterError(f"adapter did not write {output_path}")
        if output_path.stat().st_size > MAX_COMMAND_OUTPUT_BYTES:
            raise AdapterError(f"adapter output exceeds {MAX_COMMAND_OUTPUT_BYTES} bytes")

        payload = json.loads(output_path.read_text(encoding="utf-8"))
        artifacts = payload.get("artifacts", {})
        if not isinstance(artifacts, dict):
            raise AdapterError("adapter artifacts must be an object of relative paths")
        for value in artifacts.values():
            path = Path(str(value))
            if path.is_absolute() or ".." in path.parts:
                raise AdapterError("adapter artifacts must remain under the per-case output directory")
        payload.update(
            {
                "schema_version": 2,
                "case_id": inputs.case_id,
                "system": self.system,
                "adapter_version": self.version,
                "elapsed_seconds": elapsed,
            }
        )
        return RunObservation.model_validate(payload)
