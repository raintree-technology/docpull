"""Authenticated-source preflight commands."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from urllib.parse import urlparse

from rich.console import Console
from rich.markup import escape

from .core.fetcher import Fetcher
from .models.config import AuthConfig, AuthType, DocpullConfig, ProfileName
from .time_utils import utc_now_iso

AUTH_CHECK_SCHEMA_VERSION = 1


class AuthCliError(RuntimeError):
    """User-facing auth command error."""


def create_auth_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull auth",
        description="Check authenticated source access without writing fetched content",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="Validate credentials against one URL")
    check.add_argument("url")
    check.add_argument(
        "--auth-policy",
        choices=["explicit-private", "public-token-only"],
        required=True,
        help="Required explicit authenticated-source mode",
    )
    check.add_argument("--auth-bearer", metavar="TOKEN")
    check.add_argument("--auth-basic", metavar="USER:PASS")
    check.add_argument("--auth-cookie", metavar="COOKIE")
    check.add_argument("--auth-header", nargs=2, metavar=("NAME", "VALUE"))
    check.add_argument("--json", action="store_true", dest="json_output")
    check.add_argument("--output", type=Path, help="Optional non-secret JSON audit path")
    return parser


def run_auth_cli(argv: list[str] | None = None) -> int:
    parser = create_auth_parser()
    args = parser.parse_args(argv)
    console = Console()
    try:
        if args.command == "check":
            payload = asyncio.run(
                auth_check(
                    args.url,
                    auth_policy=args.auth_policy,
                    auth_bearer=args.auth_bearer,
                    auth_basic=args.auth_basic,
                    auth_cookie=args.auth_cookie,
                    auth_header=args.auth_header,
                )
            )
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            if args.json_output:
                console.print_json(data=payload)
            else:
                state = "ok" if payload["ok"] else "failed"
                console.print(
                    f"[green]Auth check {state}:[/green] {payload['url']} "
                    f"({payload['auth_type']}, status {payload.get('status_code')})"
                )
                console.print("Secret handling: credential values were not printed or persisted.")
            return 0 if payload["ok"] else 1
        parser.error(f"Unknown auth command: {args.command}")
    except AuthCliError as err:
        console.print("[red]Auth error:[/red] " + escape(str(err)))
        return 1
    except Exception as err:  # noqa: BLE001
        console.print("[red]Auth check failed:[/red] " + escape(str(err)))
        return 1
    return 1


async def auth_check(
    url: str,
    *,
    auth_policy: str,
    auth_bearer: str | None = None,
    auth_basic: str | None = None,
    auth_cookie: str | None = None,
    auth_header: list[str] | tuple[str, str] | None = None,
) -> dict[str, object]:
    """Fetch one authenticated URL without saving content or secret values."""
    auth = _auth_kwargs(
        auth_policy=auth_policy,
        auth_bearer=auth_bearer,
        auth_basic=auth_basic,
        auth_cookie=auth_cookie,
        auth_header=auth_header,
    )
    config = DocpullConfig(url=url, profile=ProfileName.CUSTOM, auth=AuthConfig.model_validate(auth))
    async with Fetcher(config) as fetcher:
        ctx = await fetcher.fetch_one(url, save=False)

    host = urlparse(url).hostname or ""
    ok = not ctx.error and not ctx.should_skip and (ctx.status_code is None or 200 <= ctx.status_code < 400)
    return {
        "schema_version": AUTH_CHECK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "url": url,
        "host": host,
        "ok": ok,
        "auth_policy": auth_policy,
        "auth_type": auth["type"],
        "status_code": ctx.status_code,
        "content_type": ctx.content_type,
        "bytes_downloaded": ctx.bytes_downloaded,
        "skip_reason": ctx.skip_reason,
        "error": ctx.error,
        # Bandit B105 false positive: artifact metadata text, not a credential.
        "secret_handling": "Credential values are never included in this report.",  # nosec B105
    }


def _auth_kwargs(
    *,
    auth_policy: str,
    auth_bearer: str | None,
    auth_basic: str | None,
    auth_cookie: str | None,
    auth_header: list[str] | tuple[str, str] | None,
) -> dict[str, object]:
    provided = [
        bool(auth_bearer),
        bool(auth_basic),
        bool(auth_cookie),
        bool(auth_header),
    ]
    if sum(1 for item in provided if item) != 1:
        raise AuthCliError("Provide exactly one auth credential source.")

    payload: dict[str, object] = {"policy": auth_policy}
    if auth_bearer:
        payload["type"] = AuthType.BEARER.value
        payload["token"] = auth_bearer
    elif auth_basic:
        if ":" not in auth_basic:
            raise AuthCliError("--auth-basic requires USER:PASS")
        username, password = auth_basic.split(":", 1)
        payload["type"] = AuthType.BASIC.value
        payload["username"] = username
        payload["password"] = password
    elif auth_cookie:
        payload["type"] = AuthType.COOKIE.value
        payload["cookie"] = auth_cookie
    elif auth_header:
        payload["type"] = AuthType.HEADER.value
        payload["header_name"] = auth_header[0]
        payload["header_value"] = auth_header[1]
    else:
        payload["type"] = AuthType.NONE.value
    return payload
