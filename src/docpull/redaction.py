"""Deterministic local redaction helpers."""

from __future__ import annotations

import importlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .time_utils import utc_now_iso

REDACTION_SCHEMA_VERSION = 1
TEXT_SUFFIXES = {".md", ".txt", ".json", ".jsonl", ".ndjson", ".yaml", ".yml", ".csv", ".tsv"}


class RedactionError(RuntimeError):
    """Raised when redaction cannot complete."""


@dataclass(frozen=True)
class RedactionRule:
    name: str
    pattern: re.Pattern[str]
    replacement: str


@dataclass(frozen=True)
class RedactionMatch:
    name: str
    start: int
    end: int
    replacement: str
    score: float | None = None
    backend: str = "regex"


DEFAULT_REDACTION_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "enabled": True,
    "backend": "regex",
    "language": "en",
    "entities": [],
    "score_threshold": 0.0,
    "patterns": [
        {"name": "email", "regex": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"},
        {
            "name": "phone",
            "regex": r"(?<!\d)(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)",
        },
        {"name": "bearer_token", "regex": r"Bearer\s+[A-Za-z0-9._~+\-/]+=*"},
        {
            "name": "api_key_like",
            "regex": r"(?i)\b(?:api[_-]?key|secret|token)\s*[:=]\s*[A-Za-z0-9._~+\-/]{16,}",
        },
        {
            "name": "private_key",
            "regex": r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----",
        },
    ],
}


def write_default_redaction_policy(path: Path) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(DEFAULT_REDACTION_POLICY, sort_keys=False), encoding="utf-8")
    return {"schema_version": REDACTION_SCHEMA_VERSION, "path": str(path), "policy": DEFAULT_REDACTION_POLICY}


def redact_pack(
    pack_dir: Path,
    *,
    policy_path: Path | None,
    output_dir: Path,
    backend: str | None = None,
) -> dict[str, Any]:
    source = pack_dir.resolve()
    output_root = output_dir.resolve()
    if not source.exists() or not source.is_dir():
        raise RedactionError(f"Pack directory does not exist: {source}")
    if output_root == source:
        raise RedactionError("Redaction output must be a different directory")
    policy = _load_policy(policy_path)
    selected_backend = _selected_backend(policy, backend)
    rules = _compile_rules(policy)
    presidio = _presidio_detector(policy) if selected_backend in {"presidio", "hybrid"} else None
    if output_root.exists():
        shutil.rmtree(output_root)
    shutil.copytree(source, output_root)
    findings: list[dict[str, Any]] = []
    for path in sorted(output_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        matches = _matches_for_text(text, rules=rules, backend=selected_backend, presidio=presidio)
        redacted, file_counts = _redact_text(text, matches)
        if matches:
            path.write_text(redacted, encoding="utf-8")
            findings.append(
                {
                    "path": str(path.relative_to(output_root)),
                    "matches": file_counts,
                    "match_count": sum(file_counts.values()),
                }
            )
    report = {
        "schema_version": REDACTION_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "source_pack": str(source),
        "output_dir": str(output_root),
        "backend": selected_backend,
        "rule_count": len(rules),
        "finding_count": len(findings),
        "match_count": sum(sum(item["matches"].values()) for item in findings),
        "findings": findings,
        "policy_path": str(policy_path) if policy_path else None,
    }
    (output_root / "redaction.report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def scan_sensitive_content(
    pack_dir: Path,
    *,
    policy_path: Path | None = None,
    backend: str | None = None,
) -> dict[str, Any]:
    root = pack_dir.resolve()
    policy = _load_policy(policy_path)
    selected_backend = _selected_backend(policy, backend)
    rules = _compile_rules(policy)
    presidio = _presidio_detector(policy) if selected_backend in {"presidio", "hybrid"} else None
    findings: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        matches = _matches_for_text(text, rules=rules, backend=selected_backend, presidio=presidio)
        file_counts = _counts_for_matches(matches)
        if file_counts:
            findings.append(
                {
                    "path": str(path.relative_to(root)),
                    "matches": file_counts,
                    "match_count": sum(file_counts.values()),
                }
            )
    return {
        "schema_version": REDACTION_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "pack_dir": str(root),
        "backend": selected_backend,
        "rule_count": len(rules),
        "finding_count": len(findings),
        "match_count": sum(sum(item["matches"].values()) for item in findings),
        "findings": findings,
    }


def _load_policy(path: Path | None) -> dict[str, Any]:
    if path is None:
        return DEFAULT_REDACTION_POLICY
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as err:
        raise RedactionError(f"Invalid redaction policy: {err}") from err
    if not isinstance(data, dict):
        raise RedactionError("Redaction policy must be an object")
    nested = data.get("redaction")
    if isinstance(nested, dict):
        return nested
    return data


def _compile_rules(policy: dict[str, Any]) -> list[RedactionRule]:
    if policy.get("enabled", True) is False:
        return []
    patterns = policy.get("patterns")
    if not isinstance(patterns, list):
        raise RedactionError("Redaction policy patterns must be a list")
    rules: list[RedactionRule] = []
    for item in patterns:
        if not isinstance(item, dict):
            raise RedactionError("Redaction pattern must be an object")
        name = str(item.get("name") or "pattern")
        regex = str(item.get("regex") or "")
        try:
            pattern = re.compile(regex)
        except re.error as err:
            raise RedactionError(f"Invalid redaction regex {name}: {err}") from err
        rules.append(RedactionRule(name=name, pattern=pattern, replacement=f"[REDACTED:{name}]"))
    return rules


def _selected_backend(policy: dict[str, Any], override: str | None) -> str:
    selected = str(override or policy.get("backend") or "regex").strip().lower()
    if selected not in {"regex", "presidio", "hybrid"}:
        raise RedactionError("Redaction backend must be one of: regex, presidio, hybrid")
    return selected


def _matches_for_text(
    text: str,
    *,
    rules: list[RedactionRule],
    backend: str,
    presidio: Any | None,
) -> list[RedactionMatch]:
    matches: list[RedactionMatch] = []
    if backend in {"regex", "hybrid"}:
        matches.extend(_regex_matches(text, rules))
    if backend in {"presidio", "hybrid"}:
        matches.extend(_presidio_matches(text, presidio))
    return _dedupe_matches(matches)


def _regex_matches(text: str, rules: list[RedactionRule]) -> list[RedactionMatch]:
    matches: list[RedactionMatch] = []
    for rule in rules:
        for match in rule.pattern.finditer(text):
            matches.append(
                RedactionMatch(
                    name=rule.name,
                    start=match.start(),
                    end=match.end(),
                    replacement=rule.replacement,
                    backend="regex",
                )
            )
    return matches


def _presidio_detector(policy: dict[str, Any]) -> dict[str, Any]:
    try:
        module = importlib.import_module("presidio_analyzer")
        nlp_module = importlib.import_module("presidio_analyzer.nlp_engine")
    except ImportError as err:
        raise RedactionError(
            "Presidio redaction requires the optional dependency. "
            "Install it with `pip install 'docpull[presidio]'`."
        ) from err
    analyzer_class = getattr(module, "AnalyzerEngine", None)
    if analyzer_class is None:
        raise RedactionError("Installed presidio_analyzer package does not expose AnalyzerEngine.")
    nlp_artifacts_class = getattr(nlp_module, "NlpArtifacts", None)
    nlp_engine_class = getattr(nlp_module, "NlpEngine", None)
    if nlp_artifacts_class is None or nlp_engine_class is None:
        raise RedactionError("Installed presidio_analyzer package does not expose NLP engine primitives.")
    try:
        analyzer = analyzer_class(
            nlp_engine=_OfflinePresidioNlpEngine(nlp_engine_class, nlp_artifacts_class),
            supported_languages=[str(policy.get("language") or "en")],
        )
    except Exception as err:  # noqa: BLE001
        raise RedactionError(f"Could not initialize Presidio AnalyzerEngine: {err}") from err
    entities = policy.get("entities")
    if entities is not None and not isinstance(entities, list):
        raise RedactionError("Presidio redaction entities must be a list")
    return {
        "analyzer": analyzer,
        "language": str(policy.get("language") or "en"),
        "entities": [str(item) for item in entities or []] or None,
        "score_threshold": _score_threshold(policy.get("score_threshold")),
    }


def _OfflinePresidioNlpEngine(nlp_engine_class: Any, nlp_artifacts_class: Any) -> Any:  # noqa: N802
    """Build a minimal Presidio NLP engine that never downloads language models."""

    class OfflineNlpEngine(nlp_engine_class):  # type: ignore[misc, valid-type, no-any-unimported]
        def load(self) -> None:
            return None

        def is_loaded(self) -> bool:
            return True

        def process_text(self, text: str, language: str) -> Any:
            return nlp_artifacts_class([], [], [], [], self, language)

        def process_batch(
            self,
            texts: list[str],
            language: str,
            batch_size: int = 1,
            n_process: int = 1,
            **_kwargs: Any,
        ) -> Any:
            del batch_size, n_process
            for text in texts:
                yield text, self.process_text(text, language)

        def is_stopword(self, word: str, language: str) -> bool:
            del word, language
            return False

        def is_punct(self, word: str, language: str) -> bool:
            del language
            return len(word) == 1 and not word.isalnum()

        def get_supported_entities(self) -> list[str]:
            return []

        def get_supported_languages(self) -> list[str]:
            return ["en"]

    return OfflineNlpEngine()


def _presidio_matches(text: str, detector: dict[str, Any] | None) -> list[RedactionMatch]:
    if detector is None:
        return []
    analyzer = detector["analyzer"]
    try:
        results = analyzer.analyze(
            text=text,
            language=detector["language"],
            entities=detector["entities"],
        )
    except Exception as err:  # noqa: BLE001
        raise RedactionError(f"Presidio analysis failed: {err}") from err
    matches: list[RedactionMatch] = []
    threshold = float(detector["score_threshold"])
    for result in results:
        start = _int_attr(result, "start")
        end = _int_attr(result, "end")
        if start is None or end is None or start < 0 or end <= start or end > len(text):
            continue
        score = _float_attr(result, "score")
        if score is not None and score < threshold:
            continue
        entity_type = str(getattr(result, "entity_type", "PII") or "PII")
        name = f"presidio:{entity_type}"
        matches.append(
            RedactionMatch(
                name=name,
                start=start,
                end=end,
                replacement=f"[REDACTED:{entity_type}]",
                score=score,
                backend="presidio",
            )
        )
    return matches


def _redact_text(text: str, matches: list[RedactionMatch]) -> tuple[str, dict[str, int]]:
    if not matches:
        return text, {}
    chunks: list[str] = []
    cursor = 0
    applied: list[RedactionMatch] = []
    for match in _ordered_non_overlapping(matches):
        chunks.append(text[cursor : match.start])
        chunks.append(match.replacement)
        cursor = match.end
        applied.append(match)
    chunks.append(text[cursor:])
    return "".join(chunks), _counts_for_matches(applied)


def _counts_for_matches(matches: list[RedactionMatch]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for match in _ordered_non_overlapping(matches):
        counts[match.name] = counts.get(match.name, 0) + 1
    return counts


def _ordered_non_overlapping(matches: list[RedactionMatch]) -> list[RedactionMatch]:
    ordered = sorted(matches, key=lambda item: (item.start, -(item.end - item.start), item.name))
    output: list[RedactionMatch] = []
    cursor = -1
    for match in ordered:
        if match.start < cursor:
            continue
        output.append(match)
        cursor = match.end
    return output


def _dedupe_matches(matches: list[RedactionMatch]) -> list[RedactionMatch]:
    seen: set[tuple[int, int, str]] = set()
    output: list[RedactionMatch] = []
    for match in matches:
        key = (match.start, match.end, match.name)
        if key in seen:
            continue
        seen.add(key)
        output.append(match)
    return output


def _score_threshold(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        score = float(value)
    except (TypeError, ValueError) as err:
        raise RedactionError("Presidio score_threshold must be a number between 0 and 1") from err
    if score < 0 or score > 1:
        raise RedactionError("Presidio score_threshold must be a number between 0 and 1")
    return score


def _int_attr(value: Any, name: str) -> int | None:
    item = getattr(value, name, None)
    if isinstance(item, int):
        return item
    return None


def _float_attr(value: Any, name: str) -> float | None:
    item = getattr(value, name, None)
    if isinstance(item, (int, float)):
        return float(item)
    return None
