#!/usr/bin/env python3
"""Audit release-facing claims for supportable wording and evidence refs."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

CLAIM_AUDIT_SCHEMA_VERSION = 1

DEFAULT_RELEASE_FILES = (
    "README.md",
    "docs/context-pack-contract-v3.md",
    "docs/context-packs.md",
    "docs/context-ci.md",
    "docs/context-dependencies.md",
    "docs/examples/README.md",
    "docs/surface-contract.md",
)

FORBIDDEN_ABSOLUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("guarantee", re.compile(r"\bguarantee[sd]?\b", re.IGNORECASE)),
    ("fully_supports", re.compile(r"\bfully supports\b", re.IGNORECASE)),
    ("complete_browser_coverage", re.compile(r"\bcomplete browser coverage\b", re.IGNORECASE)),
    ("works_with_all", re.compile(r"\bworks with all\b", re.IGNORECASE)),
    ("perfect_accuracy", re.compile(r"\b(?:perfect|100%)[ -]?accur(?:acy|ate)\b", re.IGNORECASE)),
    ("private_portal_promise", re.compile(r"\bsupports?\s+(?:SSO|MFA|private portals?)\b", re.IGNORECASE)),
    (
        "captcha_or_stealth",
        re.compile(r"\b(?:captcha solving|stealth browsing|anti-bot bypass)\b", re.IGNORECASE),
    ),
)


def audit_claims(repo: Path, *, claims_path: Path | None = None) -> dict[str, Any]:
    repo = repo.resolve()
    claims_file = claims_path or repo / "docs" / "release-claims.json"
    issues: list[dict[str, Any]] = []
    scanned_files = _existing_release_files(repo)
    for rel_path in scanned_files:
        text = (repo / rel_path).read_text(encoding="utf-8", errors="replace")
        for code, pattern in FORBIDDEN_ABSOLUTES:
            for match in pattern.finditer(text):
                line = _line_at(text, match.start())
                if _is_negative_disclaimer(line):
                    continue
                issues.append(
                    {
                        "code": "unsupported_absolute",
                        "pattern": code,
                        "path": rel_path,
                        "line": text.count("\n", 0, match.start()) + 1,
                        "excerpt": line,
                    }
                )

    claims_payload = _read_json(claims_file, issues)
    claims = claims_payload.get("claims") if isinstance(claims_payload, dict) else None
    if not isinstance(claims, list) or not claims:
        issues.append(
            {
                "code": "missing_claim_manifest",
                "path": str(
                    claims_file.relative_to(repo) if claims_file.is_relative_to(repo) else claims_file
                ),
                "message": "docs/release-claims.json must contain a non-empty claims list.",
            }
        )
        claims = []

    areas: set[str] = set()
    for index, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            issues.append({"code": "invalid_claim", "index": index, "message": "claim must be an object"})
            continue
        area = str(claim.get("area") or "").strip()
        text = str(claim.get("claim") or "").strip()
        refs = claim.get("references")
        if not area:
            issues.append({"code": "claim_missing_area", "index": index})
        else:
            areas.add(area)
        if not text:
            issues.append({"code": "claim_missing_text", "index": index, "area": area})
        if not isinstance(refs, list) or not refs:
            issues.append({"code": "claim_missing_references", "index": index, "area": area})
            continue
        ref_types = {str(ref.get("type") or "") for ref in refs if isinstance(ref, dict)}
        if not {"code", "test", "doc"} <= ref_types:
            issues.append(
                {
                    "code": "claim_reference_types",
                    "index": index,
                    "area": area,
                    "message": "each claim needs at least one code, test, and doc reference",
                }
            )
        for ref_index, ref in enumerate(refs, start=1):
            if not isinstance(ref, dict):
                issues.append(
                    {
                        "code": "invalid_claim_reference",
                        "index": index,
                        "reference_index": ref_index,
                        "area": area,
                    }
                )
                continue
            rel = str(ref.get("path") or "").strip()
            if not rel:
                issues.append(
                    {
                        "code": "claim_reference_missing_path",
                        "index": index,
                        "reference_index": ref_index,
                        "area": area,
                    }
                )
                continue
            if rel.startswith("/") or ".." in Path(rel).parts:
                issues.append(
                    {
                        "code": "claim_reference_unsafe_path",
                        "index": index,
                        "reference_index": ref_index,
                        "area": area,
                        "path": rel,
                    }
                )
                continue
            if not (repo / rel).exists():
                issues.append(
                    {
                        "code": "claim_reference_missing",
                        "index": index,
                        "reference_index": ref_index,
                        "area": area,
                        "path": rel,
                    }
                )

    return {
        "schema_version": CLAIM_AUDIT_SCHEMA_VERSION,
        "passed": not issues,
        "claim_count": len(claims),
        "area_count": len(areas),
        "areas": sorted(areas),
        "scanned_files": scanned_files,
        "claims_path": str(claims_file),
        "issues": issues,
    }


def _existing_release_files(repo: Path) -> list[str]:
    return [rel for rel in DEFAULT_RELEASE_FILES if (repo / rel).exists()]


def _read_json(path: Path, issues: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        issues.append({"code": "claims_file_missing", "path": str(path)})
        return {}
    except json.JSONDecodeError as err:
        issues.append({"code": "claims_file_invalid_json", "path": str(path), "message": str(err)})
        return {}
    return payload if isinstance(payload, dict) else {}


def _line_at(text: str, offset: int) -> str:
    start = text.rfind("\n", 0, offset) + 1
    end = text.find("\n", offset)
    if end == -1:
        end = len(text)
    return text[start:end].strip()[:240]


def _is_negative_disclaimer(line: str) -> bool:
    lowered = line.lower()
    return any(
        phrase in lowered
        for phrase in (
            "does not claim",
            "do not claim",
            "doesn't claim",
            "is not guaranteed",
            "not guaranteed",
            "not required",
        )
    )


def create_parser() -> argparse.ArgumentParser:
    repo_default = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=repo_default)
    parser.add_argument("--claims", type=Path)
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = create_parser().parse_args(argv)
    payload = audit_claims(args.repo, claims_path=args.claims)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        status = "passed" if payload["passed"] else "failed"
        print(f"claim audit {status}: {payload['claim_count']} claims, {len(payload['issues'])} issues")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
