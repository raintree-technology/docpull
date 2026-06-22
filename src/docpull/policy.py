"""Typed source policy contracts for local DocPull artifacts."""

from __future__ import annotations

import json
import re
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .time_utils import utc_now_iso

POLICY_SCHEMA_VERSION = 1
MAX_POLICY_BYTES = 1_000_000
SECRET_KEY_TOKENS = (
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
)


class PolicyError(ValueError):
    """User-facing policy parse or validation error."""


def normalize_policy_domain(value: str) -> str:
    """Normalize and validate a policy domain entry."""
    domain = value.strip().lower().rstrip(".")
    if not domain:
        raise ValueError("domain cannot be empty")
    if "://" in domain or "/" in domain or "?" in domain or "#" in domain:
        raise ValueError("domain entries must be hostnames, not URLs")
    if any(char.isspace() for char in domain):
        raise ValueError("domain entries must not contain whitespace")
    if domain.startswith("*."):
        suffix = domain[2:]
        if not suffix or "." not in suffix:
            raise ValueError("wildcard domains must use the form *.example.com")
        return domain
    if "*" in domain:
        raise ValueError("wildcards are only supported as a leading *. prefix")
    return domain


def policy_domain_matches(hostname: str, policy_domain: str) -> bool:
    """Return whether a hostname matches an exact, suffix, or wildcard policy domain."""
    host = normalize_policy_domain(hostname)
    domain = normalize_policy_domain(policy_domain)
    if domain.startswith("*."):
        suffix = domain[2:]
        return host == suffix or host.endswith(f".{suffix}")
    return host == domain or host.endswith(f".{domain}")


def reject_secret_like_mapping(value: Any, path: str = "policy") -> Any:
    """Reject mappings that look like they contain secret material."""
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).lower().replace("-", "_")
            if any(token in key_text for token in SECRET_KEY_TOKENS):
                raise ValueError(f"{path}.{key} looks like a secret field and cannot be persisted")
            reject_secret_like_mapping(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            reject_secret_like_mapping(item, f"{path}[{index}]")
    return value


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        output.append(item)
        seen.add(item)
    return output


class RenderPolicy(BaseModel):
    """Browser-rendering policy constraints."""

    backend: Literal["off", "agent-browser"] = "off"
    mode: Literal["off", "always", "fallback"] = "off"
    timeout_seconds: int | None = Field(None, ge=1)
    allowed_domains: list[str] = Field(default_factory=list)
    wait_for: str | None = None
    max_html_bytes: int | None = Field(None, ge=1)

    model_config = {"extra": "forbid"}

    @field_validator("allowed_domains", mode="before")
    @classmethod
    def _validate_domains(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("allowed_domains must be a list")
        return _dedupe([normalize_policy_domain(str(item)) for item in value])

    @model_validator(mode="after")
    def _validate_render_mode(self) -> RenderPolicy:
        if self.mode != "off" and self.backend == "off":
            raise ValueError("render.backend must be set when render.mode is not off")
        if self.backend != "off" and self.mode == "off":
            raise ValueError("render.mode must be always or fallback when render.backend is set")
        return self


class ProviderPolicy(BaseModel):
    """Provider-use policy constraints."""

    allowed: list[Literal["parallel", "tavily", "exa", "brave", "local"]] = Field(default_factory=list)
    max_estimated_cost_usd: float | None = Field(None, ge=0)
    provider_options: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}

    @field_validator("allowed", mode="before")
    @classmethod
    def _validate_allowed(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("providers.allowed must be a list")
        return _dedupe([str(item).strip().lower() for item in value])

    @field_validator("provider_options")
    @classmethod
    def _reject_secret_options(cls, value: dict[str, Any]) -> dict[str, Any]:
        reject_secret_like_mapping(value, "providers.provider_options")
        return value


class AuthPolicy(BaseModel):
    """Authentication boundary for source fetching."""

    allow_authenticated_sources: bool = False

    model_config = {"extra": "forbid"}


class RedactionPattern(BaseModel):
    """Named regular expression for artifact redaction."""

    name: str
    regex: str

    model_config = {"extra": "forbid"}

    @field_validator("regex")
    @classmethod
    def _validate_regex(cls, value: str) -> str:
        try:
            re.compile(value)
        except re.error as err:
            raise ValueError(f"invalid redaction regex: {err}") from err
        return value


class RedactionPolicy(BaseModel):
    """Redaction policy for generated artifacts."""

    enabled: bool = True
    patterns: list[RedactionPattern] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class FreshnessPolicy(BaseModel):
    """Freshness and cache constraints for source handling."""

    max_age_seconds: int | None = Field(None, ge=0)
    force_live: bool = False
    cache_allowed: bool = True
    after_date: str | None = None

    model_config = {"extra": "forbid"}

    @field_validator("after_date")
    @classmethod
    def _validate_after_date(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from datetime import date

        try:
            date.fromisoformat(value)
        except ValueError as err:
            raise ValueError("after_date must use YYYY-MM-DD") from err
        return value


class BudgetPolicy(BaseModel):
    """Paid-capable provider/cloud budget constraints."""

    maximum_paid_cost_usd: float | None = Field(None, ge=0)

    model_config = {"extra": "forbid"}


class PolicyConfig(BaseModel):
    """Reusable source, provider, auth, render, freshness, and redaction policy."""

    schema_version: Literal[1] = 1
    allowed_domains: list[str] = Field(default_factory=list)
    denied_domains: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)
    denied_paths: list[str] = Field(default_factory=list)
    max_pages: int | None = Field(None, ge=1)
    max_depth: int | None = Field(None, ge=1)
    render: RenderPolicy = Field(default_factory=RenderPolicy)
    providers: ProviderPolicy = Field(default_factory=ProviderPolicy)
    auth: AuthPolicy = Field(default_factory=AuthPolicy)
    redaction: RedactionPolicy = Field(default_factory=RedactionPolicy)
    freshness: FreshnessPolicy = Field(default_factory=FreshnessPolicy)
    budget: BudgetPolicy = Field(default_factory=BudgetPolicy)

    model_config = {"extra": "forbid"}

    @field_validator("allowed_domains", "denied_domains", mode="before")
    @classmethod
    def _validate_domains(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("domain policy fields must be lists")
        return _dedupe([normalize_policy_domain(str(item)) for item in value])

    @field_validator("allowed_paths", "denied_paths", mode="before")
    @classmethod
    def _validate_paths(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise ValueError("path policy fields must be lists")
        paths = [str(item).strip() for item in value]
        for path in paths:
            if not path.startswith("/"):
                raise ValueError("path policy patterns must start with /")
        return _dedupe(paths)

    @model_validator(mode="after")
    def _validate_consistency(self) -> PolicyConfig:
        overlap = set(self.allowed_domains) & set(self.denied_domains)
        if overlap:
            blocked = ", ".join(sorted(overlap))
            raise ValueError(f"domains cannot be both allowed and denied: {blocked}")
        return self

    @classmethod
    def from_file(cls, path: Path) -> PolicyConfig:
        """Parse and validate a JSON or YAML policy file."""
        if not path.exists():
            raise PolicyError(f"Policy file does not exist: {path}")
        raw = path.read_text(encoding="utf-8")
        if len(raw.encode("utf-8")) > MAX_POLICY_BYTES:
            raise PolicyError(f"Policy file exceeds {MAX_POLICY_BYTES} bytes: {path}")
        try:
            if path.suffix.lower() == ".json":
                data = json.loads(raw)
            else:
                import yaml

                data = yaml.safe_load(raw)
        except Exception as err:  # noqa: BLE001
            raise PolicyError(f"Could not parse policy file {path}: {err}") from err
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise PolicyError("Policy file must contain a JSON/YAML object")
        try:
            return cls.model_validate(data)
        except ValidationError as err:
            messages: list[str] = []
            for issue in err.errors(include_input=False):
                loc = ".".join(str(part) for part in issue.get("loc", ())) or "policy"
                messages.append(f"{loc}: {issue.get('msg', 'invalid value')}")
            raise PolicyError("; ".join(messages)) from err
        except Exception as err:  # noqa: BLE001
            raise PolicyError(str(err)) from err

    def allows_url(self, url: str) -> tuple[bool, str | None]:
        """Check a URL against deterministic domain and path constraints."""
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        if not parsed.scheme.startswith("http") or not host:
            return False, "not_http_url"
        if self.allowed_domains and not any(
            policy_domain_matches(host, domain) for domain in self.allowed_domains
        ):
            return False, "domain_not_allowed"
        if self.denied_domains and any(policy_domain_matches(host, domain) for domain in self.denied_domains):
            return False, "domain_denied"
        path = parsed.path or "/"
        if self.allowed_paths and not any(fnmatch(path, pattern) for pattern in self.allowed_paths):
            return False, "path_not_allowed"
        if self.denied_paths and any(fnmatch(path, pattern) for pattern in self.denied_paths):
            return False, "path_denied"
        return True, None

    def explain(self) -> list[str]:
        """Return human-readable policy effects."""
        lines: list[str] = [f"schema_version: {self.schema_version}"]
        if self.allowed_domains:
            lines.append("allowed domains: " + ", ".join(self.allowed_domains))
        else:
            lines.append("allowed domains: any public HTTP(S) source allowed by runtime URL validation")
        if self.denied_domains:
            lines.append("denied domains: " + ", ".join(self.denied_domains))
        if self.allowed_paths:
            lines.append("allowed paths: " + ", ".join(self.allowed_paths))
        if self.denied_paths:
            lines.append("denied paths: " + ", ".join(self.denied_paths))
        lines.append(f"max pages: {self.max_pages if self.max_pages is not None else 'not set'}")
        lines.append(
            "providers: "
            + (
                ", ".join(self.providers.allowed)
                if self.providers.allowed
                else "disabled unless explicitly selected outside this policy"
            )
        )
        if self.providers.max_estimated_cost_usd is not None:
            lines.append(f"provider cost guard: ${self.providers.max_estimated_cost_usd:.4f}")
        if self.budget.maximum_paid_cost_usd is not None:
            lines.append(f"paid budget: ${self.budget.maximum_paid_cost_usd:.4f}")
        lines.append(
            "rendering: "
            f"{self.render.mode} via {self.render.backend}"
            + (f" ({self.render.timeout_seconds}s timeout)" if self.render.timeout_seconds else "")
        )
        lines.append(
            "auth: "
            + (
                "authenticated sources allowed"
                if self.auth.allow_authenticated_sources
                else "authenticated sources disabled"
            )
        )
        lines.append(
            "redaction: "
            + (
                f"enabled with {len(self.redaction.patterns)} pattern(s)"
                if self.redaction.enabled
                else "disabled"
            )
        )
        lines.append(
            "freshness: "
            + (
                f"max_age={self.freshness.max_age_seconds}s"
                if self.freshness.max_age_seconds is not None
                else "no max age"
            )
            + (", force_live" if self.freshness.force_live else "")
            + (", cache disallowed" if not self.freshness.cache_allowed else "")
        )
        lines.append(
            "secret handling: policy artifacts never include provider keys, auth tokens, cookies, "
            "or passwords"
        )
        return lines

    def to_source_policy_payload(
        self,
        *,
        generated_at: str | None = None,
        source: str = "policy-config",
        url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the persisted, non-secret source_policy.json payload."""
        payload = self.model_dump(mode="json", exclude_none=True)
        reject_secret_like_mapping(payload, "source_policy")
        return {
            "schema_version": POLICY_SCHEMA_VERSION,
            "generated_at": generated_at or utc_now_iso(),
            "url": url,
            "source": source,
            "constraints": payload,
            "explain": self.explain(),
            "metadata": metadata or {},
            # Bandit B105 false positive: artifact metadata text, not a credential.
            "secret_handling": (
                "No secrets, provider keys, auth tokens, cookies, or passwords are stored in this artifact."  # nosec B105
            ),
        }
