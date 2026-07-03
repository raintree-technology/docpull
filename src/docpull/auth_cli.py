"""Authenticated-source preflight commands."""

from __future__ import annotations

import argparse
import asyncio
import base64
import ipaddress
import json
import urllib.error
import urllib.request
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
    check.add_argument(
        "--allow-insecure-local-http",
        action="store_true",
        help=argparse.SUPPRESS,
    )
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
                    allow_insecure_local_http=args.allow_insecure_local_http,
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
    allow_insecure_local_http: bool = False,
) -> dict[str, object]:
    """Fetch one authenticated URL without saving content or secret values."""
    auth = _auth_kwargs(
        auth_policy=auth_policy,
        auth_bearer=auth_bearer,
        auth_basic=auth_basic,
        auth_cookie=auth_cookie,
        auth_header=auth_header,
    )
    if allow_insecure_local_http:
        if not _is_loopback_http_url(url):
            raise AuthCliError(
                "--allow-insecure-local-http is restricted to http://localhost or loopback URLs"
            )
        return await asyncio.to_thread(_local_http_auth_check, url, auth_policy, auth)

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


def _local_http_auth_check(url: str, auth_policy: str, auth: dict[str, object]) -> dict[str, object]:
    """Run the hidden release-smoke auth path against loopback HTTP only."""
    headers = _headers_from_auth(auth)
    request = urllib.request.Request(url, headers=headers)
    status_code: int | None = None
    content_type: str | None = None
    bytes_downloaded = 0
    error: str | None = None
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
            data = response.read(1024)
            status_code = int(response.status)
            content_type = response.headers.get("content-type")
            bytes_downloaded = len(data)
    except urllib.error.HTTPError as err:
        data = err.read(1024)
        status_code = int(err.code)
        content_type = err.headers.get("content-type")
        bytes_downloaded = len(data)
    except OSError as err:
        error = str(err)

    host = urlparse(url).hostname or ""
    ok = error is None and status_code is not None and 200 <= status_code < 400
    return {
        "schema_version": AUTH_CHECK_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "url": url,
        "host": host,
        "ok": ok,
        "auth_policy": auth_policy,
        "auth_type": auth["type"],
        "status_code": status_code,
        "content_type": content_type,
        "bytes_downloaded": bytes_downloaded,
        "skip_reason": None,
        "error": error,
        "secret_handling": "Credential values are never included in this report.",  # nosec B105
    }


def _headers_from_auth(auth: dict[str, object]) -> dict[str, str]:
    auth_type = str(auth.get("type") or AuthType.NONE.value)
    if auth_type == AuthType.BEARER.value and auth.get("token"):
        return {"Authorization": f"Bearer {auth['token']}"}
    if auth_type == AuthType.BASIC.value and auth.get("username") and auth.get("password"):
        credentials = f"{auth['username']}:{auth['password']}"
        encoded = base64.b64encode(credentials.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    if auth_type == AuthType.COOKIE.value and auth.get("cookie"):
        return {"Cookie": str(auth["cookie"])}
    if auth_type == AuthType.HEADER.value and auth.get("header_name") and auth.get("header_value"):
        return {str(auth["header_name"]): str(auth["header_value"])}
    return {}


def _is_loopback_http_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "http" or not parsed.hostname:
        return False
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


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
