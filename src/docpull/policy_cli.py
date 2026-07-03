"""CLI commands for DocPull policy files."""

from __future__ import annotations

import argparse
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from .policy import PolicyConfig, PolicyError
from .time_utils import utc_now_iso


def create_policy_parser() -> argparse.ArgumentParser:
    """Create the ``docpull policy`` parser."""
    parser = argparse.ArgumentParser(
        prog="docpull policy",
        description="Validate and explain DocPull source policy files",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="Validate a JSON or YAML policy file")
    validate.add_argument("policy", type=Path)
    validate.add_argument("--json", action="store_true", dest="json_output")

    explain = subparsers.add_parser("explain", help="Explain a policy file's effective constraints")
    explain.add_argument("policy", type=Path)
    explain.add_argument("--json", action="store_true", dest="json_output")

    redaction = subparsers.add_parser("redaction", help="Manage redaction policy helpers")
    redaction_subparsers = redaction.add_subparsers(dest="redaction_command", required=True)
    redaction_init = redaction_subparsers.add_parser("init", help="Write a default redaction policy")
    redaction_init.add_argument("--output", "-o", type=Path, default=Path("redaction.yml"))
    redaction_init.add_argument("--json", action="store_true", dest="json_output")

    return parser


def run_policy_cli(argv: list[str] | None = None) -> int:
    """Run ``docpull policy``."""
    parser = create_policy_parser()
    args = parser.parse_args(argv)
    console = Console()

    if args.command == "redaction":
        from .redaction import RedactionError, write_default_redaction_policy

        try:
            payload = write_default_redaction_policy(args.output)
        except RedactionError as err:
            console.print("[red]Policy error:[/red] " + escape(str(err)))
            return 1
        if args.json_output:
            console.print_json(data=payload)
        else:
            console.print(f"[green]Redaction policy written:[/green] {payload['path']}")
        return 0

    try:
        policy = PolicyConfig.from_file(args.policy)
    except PolicyError as err:
        console.print("[red]Policy error:[/red] " + escape(str(err)))
        return 1

    generated_at = utc_now_iso()
    if args.command == "validate":
        if args.json_output:
            console.print_json(
                data={
                    "schema_version": 1,
                    "generated_at": generated_at,
                    "valid": True,
                    "policy_path": str(args.policy),
                    "source_policy": policy.to_source_policy_payload(
                        generated_at=generated_at,
                        source="policy-validate",
                    ),
                }
            )
        else:
            console.print(f"[green]Policy valid:[/green] {args.policy}")
            console.print(
                "Secret handling: no provider keys, auth tokens, cookies, or passwords are persisted."
            )
        return 0

    if args.command == "explain":
        explanation = policy.explain()
        if args.json_output:
            console.print_json(
                data={
                    "schema_version": 1,
                    "generated_at": generated_at,
                    "policy_path": str(args.policy),
                    "explain": explanation,
                }
            )
        else:
            console.print(f"[bold]Policy explanation:[/bold] {args.policy}")
            for line in explanation:
                console.print(f"- {line}")
        return 0

    parser.error(f"Unknown policy command: {args.command}")
    return 1
