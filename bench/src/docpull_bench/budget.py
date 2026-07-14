"""Thread-safe fail-closed budget accounting for paid benchmark adapters."""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass, field


class BudgetError(RuntimeError):
    """A planned or attempted provider request exceeds the operator ceiling."""


@dataclass
class BudgetLedger:
    maximum_usd: float
    planned_usd: float = 0.0
    reserved_usd: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self) -> None:
        if not math.isfinite(self.maximum_usd) or self.maximum_usd < 0:
            raise BudgetError("maximum budget must be a finite non-negative number")

    def plan(self, amount: float) -> None:
        self._validate(amount)
        with self._lock:
            if amount > self.maximum_usd + 1e-12:
                raise BudgetError(
                    f"conservative preflight estimate ${amount:.6f} exceeds "
                    f"--max-cost-usd ${self.maximum_usd:.6f}; no requests were made"
                )
            self.planned_usd = amount

    def reserve(self, amount: float) -> None:
        self._validate(amount)
        with self._lock:
            proposed = self.reserved_usd + amount
            if proposed > self.maximum_usd + 1e-12:
                raise BudgetError(
                    f"request reservation would raise benchmark spend ceiling to ${proposed:.6f}, "
                    f"above --max-cost-usd ${self.maximum_usd:.6f}; request was not made"
                )
            self.reserved_usd = proposed

    @staticmethod
    def _validate(amount: float) -> None:
        if not math.isfinite(amount) or amount < 0:
            raise BudgetError("budget amount must be a finite non-negative number")
