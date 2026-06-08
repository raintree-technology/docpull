"""Shared optional live-provider key discovery for docpull."""

from __future__ import annotations

import json
import os
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DOCPULL_CONFIG_DIR_NAME = "docpull"
SECRETS_FILENAME = "secrets.env"
PROJECT_ENV_FILENAME = ".env.local"

ProviderName = Literal["parallel", "tavily", "exa"]
PROVIDER_NAMES: tuple[ProviderName, ...] = ("parallel", "tavily", "exa")


@dataclass(frozen=True)
class ProviderConfig:
    """Non-secret provider metadata."""

    name: ProviderName
    label: str
    api_key_env_var: str


@dataclass(frozen=True)
class ProviderApiKeyLookup:
    """Non-secret provider API-key lookup result."""

    value: str | None
    source: str
    path: Path | None = None


PROVIDER_CONFIGS: dict[ProviderName, ProviderConfig] = {
    "parallel": ProviderConfig(
        name="parallel",
        label="Parallel",
        api_key_env_var="PARALLEL_API_KEY",
    ),
    "tavily": ProviderConfig(
        name="tavily",
        label="Tavily",
        api_key_env_var="TAVILY_API_KEY",
    ),
    "exa": ProviderConfig(
        name="exa",
        label="Exa",
        api_key_env_var="EXA_API_KEY",
    ),
}


def normalize_provider_name(value: str) -> ProviderName:
    """Return a canonical provider name or raise ``ValueError``."""

    provider = value.strip().lower()
    if provider not in PROVIDER_CONFIGS:
        raise ValueError(f"Unsupported live provider: {value}")
    return provider  # type: ignore[return-value]


def clean_api_key(value: str | None) -> str | None:
    cleaned = (value or "").strip()
    return cleaned or None


def user_secrets_path() -> Path:
    xdg_home = clean_api_key(os.environ.get("XDG_CONFIG_HOME"))
    if xdg_home:
        return Path(xdg_home) / DOCPULL_CONFIG_DIR_NAME / SECRETS_FILENAME
    return Path.home() / ".config" / DOCPULL_CONFIG_DIR_NAME / SECRETS_FILENAME


def find_project_env_path(start: Path) -> Path | None:
    current = start.resolve()
    for directory in (current, *current.parents):
        candidate = directory / PROJECT_ENV_FILENAME
        if candidate.exists():
            return candidate
        if (directory / ".git").exists():
            break
    return None


def lookup_provider_api_key(provider: ProviderName | str) -> ProviderApiKeyLookup:
    config = PROVIDER_CONFIGS[normalize_provider_name(provider)]
    env_key = clean_api_key(os.environ.get(config.api_key_env_var))
    if env_key:
        return ProviderApiKeyLookup(value=env_key, source="env")

    project_path = find_project_env_path(Path.cwd())
    if project_path:
        project_key = read_key_file(project_path, config.api_key_env_var)
        if project_key:
            return ProviderApiKeyLookup(value=project_key, source="project_env", path=project_path)

    user_path = user_secrets_path()
    user_key = read_key_file(user_path, config.api_key_env_var)
    if user_key:
        return ProviderApiKeyLookup(value=user_key, source="user_config", path=user_path)

    return ProviderApiKeyLookup(value=None, source="missing")


def lookup_api_key_env_var(env_var: str) -> ProviderApiKeyLookup:
    for provider, config in PROVIDER_CONFIGS.items():
        if config.api_key_env_var == env_var:
            return lookup_provider_api_key(provider)

    env_key = clean_api_key(os.environ.get(env_var))
    if env_key:
        return ProviderApiKeyLookup(value=env_key, source="env")

    project_path = find_project_env_path(Path.cwd())
    if project_path:
        project_key = read_key_file(project_path, env_var)
        if project_key:
            return ProviderApiKeyLookup(value=project_key, source="project_env", path=project_path)

    user_path = user_secrets_path()
    user_key = read_key_file(user_path, env_var)
    if user_key:
        return ProviderApiKeyLookup(value=user_key, source="user_config", path=user_path)

    return ProviderApiKeyLookup(value=None, source="missing")


def read_key_file(path: Path, env_var: str) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        parsed = parse_env_assignment(line)
        if parsed and parsed[0] == env_var:
            return clean_api_key(parsed[1])
    return None


def write_provider_secret(provider: ProviderName | str, path: Path, api_key: str, *, force: bool) -> None:
    config = PROVIDER_CONFIGS[normalize_provider_name(provider)]
    write_key_file(path, config.api_key_env_var, api_key, force=force)


def write_key_file(path: Path, env_var: str, api_key: str, *, force: bool) -> None:
    existing = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    output_lines: list[str] = []
    replaced = False
    for line in existing:
        parsed = parse_env_assignment(line)
        if parsed and parsed[0] == env_var:
            if not force:
                raise FileExistsError(f"{path} already contains {env_var}; pass --force to overwrite it.")
            if not replaced:
                output_lines.append(key_assignment(env_var, api_key))
                replaced = True
            continue
        output_lines.append(line)
    if not replaced:
        output_lines.append(key_assignment(env_var, api_key))

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.name == SECRETS_FILENAME:
        chmod_best_effort(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(output_lines).rstrip() + "\n")
    chmod_best_effort(path, 0o600)


def parse_env_assignment(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    name, value = stripped.split("=", 1)
    name = name.strip()
    if not name:
        return None
    return name, unquote_env_value(value.strip())


def unquote_env_value(value: str) -> str:
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            pass
        else:
            return parsed if isinstance(parsed, str) else str(parsed)
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
        if value:
            value = value.replace("\\'", "'").replace('\\"', '"')
    return value


def key_assignment(env_var: str, api_key: str) -> str:
    return f"{env_var}={quote_env_value(api_key)}"


def quote_env_value(value: str) -> str:
    return json.dumps(value)


def chmod_best_effort(path: Path, mode: int) -> None:
    with suppress(OSError):
        path.chmod(mode)
