"""Generate or verify the JSON Schemas shipped for cross-repository contracts."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from docpull.contracts import CONTRACT_MODELS  # noqa: E402

SCHEMA_DIR = ROOT / "src" / "docpull" / "schemas"


def expected_schemas() -> dict[Path, str]:
    expected: dict[Path, str] = {}
    for filename, model in sorted(CONTRACT_MODELS.items()):
        payload = model.model_json_schema(mode="serialization")
        payload["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        payload["$id"] = f"https://docpull.dev/schemas/{filename}"
        expected[SCHEMA_DIR / filename] = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    return expected


def check() -> int:
    drifted = [
        path
        for path, expected in expected_schemas().items()
        if not path.exists() or path.read_text(encoding="utf-8") != expected
    ]
    unexpected = sorted(path for path in SCHEMA_DIR.glob("*.schema.json") if path not in expected_schemas())
    if drifted or unexpected:
        print("Contract schemas are stale:", file=sys.stderr)
        for path in [*drifted, *unexpected]:
            print(f"  - {path.relative_to(ROOT)}", file=sys.stderr)
        return 1
    print(f"Contract schemas are synchronized ({len(CONTRACT_MODELS)} schemas).")
    return 0


def write() -> int:
    SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
    expected = expected_schemas()
    for path, content in expected.items():
        path.write_text(content, encoding="utf-8")
    for path in SCHEMA_DIR.glob("*.schema.json"):
        if path not in expected:
            path.unlink()
    print(f"Wrote {len(expected)} contract schemas to {SCHEMA_DIR.relative_to(ROOT)}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    return check() if args.check else write()


if __name__ == "__main__":
    raise SystemExit(main())
