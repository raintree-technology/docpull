"""Load versioned provider cost assumptions used only for fail-closed ceilings."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from .models import StrictModel
from .serialization import strict_yaml_load


class PriceEntry(StrictModel):
    unit: str
    usd: float = Field(ge=0)
    kind: str
    source: str


class PricingSnapshot(StrictModel):
    schema_version: int
    snapshot: str
    effective_date: str
    providers: dict[str, dict[str, PriceEntry]]

    @classmethod
    def load(cls, path: Path | None = None) -> PricingSnapshot:
        source = path or Path(__file__).resolve().parents[2] / "pricing" / "providers.yaml"
        return cls.model_validate(strict_yaml_load(source.read_text(encoding="utf-8")))

    def price(self, provider: str, operation: str) -> PriceEntry:
        try:
            return self.providers[provider][operation]
        except KeyError as error:
            raise ValueError(f"pricing snapshot has no {provider}.{operation} entry") from error

    def public_entry(self, provider: str, operation: str) -> dict[str, Any]:
        return self.price(provider, operation).model_dump(mode="json")
