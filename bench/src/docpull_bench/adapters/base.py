"""Adapter protocol shared by every black-box system under test."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from ..models import BenchmarkInput, Lane, RunObservation


class AdapterError(RuntimeError):
    """An adapter could not execute or normalize its system."""


class SystemAdapter(Protocol):
    system: str
    version: str
    capabilities: frozenset[Lane]
    cache_policy: str
    retry_policy: str
    pricing_snapshot: str | None

    def preflight(self, inputs: list[BenchmarkInput], *, repeat: int) -> None: ...

    def run(self, inputs: BenchmarkInput, output_root: Path) -> RunObservation: ...

    def public_config(self) -> dict[str, Any]: ...
