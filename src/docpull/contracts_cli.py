"""CLI for inspecting and exporting DocPull cross-repository schemas."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import CONTRACT_MODELS, bundled_schema_path, write_contract_schemas


def run_contracts_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="docpull contracts", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="List bundled contract schemas")
    list_parser.add_argument("--json", action="store_true", dest="json_output")
    export = subparsers.add_parser("export", help="Export all contract schemas")
    export.add_argument("--output-dir", "-o", type=Path, required=True)
    show = subparsers.add_parser("show", help="Print one bundled JSON Schema")
    show.add_argument("name", choices=sorted(CONTRACT_MODELS))
    args = parser.parse_args(argv)

    if args.command == "list":
        names = sorted(CONTRACT_MODELS)
        if args.json_output:
            print(json.dumps({"schemas": names}, indent=2))
        else:
            print("\n".join(names))
        return 0
    if args.command == "export":
        paths = write_contract_schemas(args.output_dir.resolve())
        print(f"Exported {len(paths)} schemas to {args.output_dir.resolve()}")
        return 0
    if args.command == "show":
        print(bundled_schema_path(args.name).read_text(encoding="utf-8"), end="")
        return 0
    return 1


__all__ = ["run_contracts_cli"]
