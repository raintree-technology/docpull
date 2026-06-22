"""Localhost-only ASGI app for serving DocPull packs as JSON."""

from __future__ import annotations

import argparse
import ipaddress
import json
from collections.abc import Awaitable, Callable, MutableMapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

from rich.console import Console
from rich.markup import escape

from .pack_reader import DEFAULT_DOCUMENT_LIMIT, PackReadError, load_pack

ASGIScope = MutableMapping[str, Any]
ASGIMessage = MutableMapping[str, Any]
ASGIReceive = Callable[[], Awaitable[ASGIMessage]]
ASGISend = Callable[[ASGIMessage], Awaitable[None]]

DEFAULT_SERVE_HOST = "127.0.0.1"
DEFAULT_SERVE_PORT = 8765


class PackServerError(RuntimeError):
    """User-facing local pack server error."""


class PackASGIApp:
    """Small JSON-only ASGI app backed by local pack files."""

    def __init__(self, pack_dir: Path | str, *, readonly: bool = True) -> None:
        self.pack_dir = Path(pack_dir).expanduser().resolve()
        self.readonly = readonly

    async def __call__(self, scope: ASGIScope, receive: ASGIReceive, send: ASGISend) -> None:
        scope_type = str(scope.get("type") or "")
        if scope_type == "lifespan":
            await _handle_lifespan(receive, send)
            return
        if scope_type != "http":
            await _json_response(send, 500, {"error": "Unsupported ASGI scope"})
            return

        method = str(scope.get("method") or "GET").upper()
        if method != "GET":
            await _json_response(send, 405, {"error": "Only GET endpoints are supported"})
            return

        try:
            payload, status = self._route(scope)
        except PackReadError as err:
            payload, status = {"error": str(err)}, 400
        except Exception as err:  # noqa: BLE001
            payload, status = {"error": f"Pack server failed: {err}"}, 500
        await _json_response(send, status, payload)

    def _route(self, scope: ASGIScope) -> tuple[dict[str, Any], int]:
        raw_path = str(scope.get("path") or "/")
        path = raw_path.rstrip("/") or "/"
        query = parse_qs(bytes(scope.get("query_string") or b"").decode("utf-8"))
        pack = load_pack(self.pack_dir)

        if path == "/health":
            return pack.health_payload(), 200
        if path == "/manifest":
            return pack.manifest, 200
        if path == "/documents":
            limit = _query_int(query, "limit", DEFAULT_DOCUMENT_LIMIT)
            offset = _query_int(query, "offset", 0)
            return pack.documents_payload(limit=limit, offset=offset), 200
        if path.startswith("/documents/"):
            document_id = unquote(path.removeprefix("/documents/"))
            record = pack.find_document(document_id)
            if record is None:
                return {"error": "Document not found", "document_id": document_id}, 404
            return pack.document_payload(record, include_content=True), 200
        if path == "/search":
            query_values = query.get("q") or []
            search_query = query_values[0].strip() if query_values else ""
            if not search_query:
                return {"error": "Missing required query parameter: q"}, 400
            limit = _query_int(query, "limit", 10)
            return pack.search_payload(search_query, limit=limit), 200
        if path == "/citations":
            return pack.citations_payload(), 200
        if path == "/sources":
            return {
                "schema_version": 1,
                "pack_dir": str(pack.pack_dir),
                "source_count": len(pack.sources),
                "sources": [source.to_dict() for source in pack.sources],
            }, 200
        return {"error": "Not found", "path": raw_path}, 404


def create_pack_app(pack_dir: Path | str, *, readonly: bool = True) -> PackASGIApp:
    """Create a localhost-oriented ASGI app over a DocPull pack directory."""
    return PackASGIApp(pack_dir, readonly=readonly)


def create_serve_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull serve",
        description="Serve a local DocPull pack as a localhost-only JSON API",
    )
    parser.add_argument("pack_dir", type=Path, help="Pack directory to serve")
    parser.add_argument("--host", default=DEFAULT_SERVE_HOST, help="Bind host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=DEFAULT_SERVE_PORT, help="Bind port (default: 8765)")
    parser.add_argument(
        "--readonly",
        action="store_true",
        help="Accepted for explicitness; pack serving is read-only by default",
    )
    parser.add_argument(
        "--allow-network-bind",
        action="store_true",
        help="Allow non-loopback hosts such as 0.0.0.0",
    )
    return parser


def run_serve_cli(argv: list[str] | None = None) -> int:
    parser = create_serve_parser()
    args = parser.parse_args(argv)
    console = Console()
    try:
        validate_bind_host(args.host, allow_network_bind=args.allow_network_bind)
        load_pack(args.pack_dir)
    except (PackServerError, PackReadError) as err:
        console.print("[red]Serve error:[/red] " + escape(str(err)))
        return 1

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Serve error:[/red] uvicorn is required for `docpull serve`. "
            "Install with `pip install docpull[serve]`."
        )
        return 1

    app = create_pack_app(args.pack_dir, readonly=True)
    console.print(
        f"[green]Serving:[/green] {Path(args.pack_dir).resolve()} on http://{args.host}:{args.port}"
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def validate_bind_host(host: str, *, allow_network_bind: bool = False) -> None:
    """Refuse non-localhost binds unless the caller explicitly opts in."""
    if allow_network_bind:
        return
    if _is_loopback_host(host):
        return
    raise PackServerError(
        f"Refusing non-localhost bind host {host!r}; pass --allow-network-bind to expose the pack."
    )


async def _handle_lifespan(receive: ASGIReceive, send: ASGISend) -> None:
    while True:
        message = await receive()
        message_type = message.get("type")
        if message_type == "lifespan.startup":
            await send({"type": "lifespan.startup.complete"})
        elif message_type == "lifespan.shutdown":
            await send({"type": "lifespan.shutdown.complete"})
            return


async def _json_response(send: ASGISend, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _query_int(query: dict[str, list[str]], key: str, default: int) -> int:
    values = query.get(key)
    if not values:
        return default
    try:
        return int(values[0])
    except ValueError:
        return default


def _is_loopback_host(host: str) -> bool:
    normalized = host.strip().strip("[]").lower()
    if normalized == "localhost":
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False
