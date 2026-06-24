"""Local-first report sharing for Markdown and HTML files."""

from __future__ import annotations

import argparse
import html
import json
import re
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from rich.console import Console
from rich.markup import escape

from .server import PackServerError, validate_bind_host

DEFAULT_SHARE_HOST = "127.0.0.1"
DEFAULT_SHARE_PORT = 0
SHARE_SCHEMA_VERSION = 1

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_UNORDERED_RE = re.compile(r"^\s{0,3}[-*+]\s+(.+?)\s*$")
_ORDERED_RE = re.compile(r"^\s{0,3}\d+[.)]\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)\s*([A-Za-z0-9_+.-]*)\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_LINK_RE = re.compile(r"\[([^\]]+)]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_HTML_DOCUMENT_RE = re.compile(r"<html(?:\s|>)", re.IGNORECASE)


class ShareError(RuntimeError):
    """User-facing report sharing error."""


class ReportHTTPServer(ThreadingHTTPServer):
    """Threading HTTP server with reusable sockets for report previews."""

    allow_reuse_address = True


def create_share_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docpull share",
        description="Serve a local Markdown or HTML report at a simple URL",
    )
    parser.add_argument("report", type=Path, help="Markdown or HTML report file")
    parser.add_argument("--host", default=DEFAULT_SHARE_HOST, help="Bind host (default: 127.0.0.1)")
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_SHARE_PORT,
        help="Bind port (default: 0, choose an available port)",
    )
    parser.add_argument("--title", help="Override the rendered page title")
    parser.add_argument("--open", action="store_true", help="Open the share URL in the default browser")
    parser.add_argument(
        "--allow-network-bind",
        action="store_true",
        help="Allow non-loopback hosts such as 0.0.0.0",
    )
    return parser


def run_share_cli(argv: list[str] | None = None) -> int:
    parser = create_share_parser()
    args = parser.parse_args(argv)
    console = Console()

    try:
        server = create_report_server(
            args.report,
            host=args.host,
            port=args.port,
            title=args.title,
            allow_network_bind=args.allow_network_bind,
        )
    except (ShareError, PackServerError) as err:
        console.print("[red]Share error:[/red] " + escape(str(err)))
        return 1

    url = report_url(args.host, server.server_port)
    console.print(f"[green]Sharing:[/green] {Path(args.report).expanduser().resolve()}")
    console.print(f"[green]URL:[/green] {url}")
    console.print("Press Ctrl-C to stop.")
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\nStopped sharing.")
    finally:
        server.server_close()
    return 0


def create_report_server(
    report: Path | str,
    *,
    host: str = DEFAULT_SHARE_HOST,
    port: int = DEFAULT_SHARE_PORT,
    title: str | None = None,
    allow_network_bind: bool = False,
) -> ReportHTTPServer:
    """Create a local HTTP server for one Markdown or HTML report."""
    validate_bind_host(host, allow_network_bind=allow_network_bind)
    report_path = _validate_report_path(Path(report))
    handler = _handler_for_report(report_path, title=title)
    return ReportHTTPServer((host, port), handler)


def report_url(host: str, port: int) -> str:
    """Build the browser URL printed for a report server."""
    display_host = host.strip() or DEFAULT_SHARE_HOST
    if display_host == "0.0.0.0":
        display_host = DEFAULT_SHARE_HOST
    if ":" in display_host and not display_host.startswith("["):
        display_host = f"[{display_host}]"
    return f"http://{display_host}:{port}/"


def render_report_document(report: Path | str, *, title: str | None = None) -> bytes:
    """Render a Markdown or HTML report into bytes suitable for text/html."""
    report_path = _validate_report_path(Path(report))
    text = report_path.read_text(encoding="utf-8")
    suffix = report_path.suffix.lower()
    page_title = title or _derive_title(text, fallback=report_path.stem)
    if suffix in {".html", ".htm"}:
        if _HTML_DOCUMENT_RE.search(text):
            return text.encode("utf-8")
        return _html_shell(_inline_fragment(text), title=page_title).encode("utf-8")
    body = _markdown_to_html(text)
    return _html_shell(body, title=page_title).encode("utf-8")


def _handler_for_report(report_path: Path, *, title: str | None) -> type[BaseHTTPRequestHandler]:
    class ReportRequestHandler(BaseHTTPRequestHandler):
        server_version = "DocPullShare/1"

        def do_GET(self) -> None:  # noqa: N802
            self._handle(send_body=True)

        def do_HEAD(self) -> None:  # noqa: N802
            self._handle(send_body=False)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        def _handle(self, *, send_body: bool) -> None:
            path = urlparse(self.path).path.rstrip("/") or "/"
            if path == "/health":
                self._write_json(_health_payload(report_path), send_body=send_body)
                return
            if path == "/source":
                body = report_path.read_bytes()
                if _is_markdown(report_path):
                    content_type = "text/markdown; charset=utf-8"
                else:
                    content_type = "text/html; charset=utf-8"
                self._write_response(HTTPStatus.OK, content_type, body, send_body=send_body)
                return
            if path in {"/", "/report", "/report.html"}:
                try:
                    body = render_report_document(report_path, title=title)
                except OSError as err:
                    self._write_json(
                        {"error": f"Could not read report: {err}"},
                        status=HTTPStatus.INTERNAL_SERVER_ERROR,
                    )
                    return
                self._write_response(HTTPStatus.OK, "text/html; charset=utf-8", body, send_body=send_body)
                return
            self._write_json(
                {"error": "Not found", "path": path},
                status=HTTPStatus.NOT_FOUND,
                send_body=send_body,
            )

        def _write_json(
            self,
            payload: dict[str, Any],
            *,
            status: HTTPStatus = HTTPStatus.OK,
            send_body: bool = True,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            self._write_response(status, "application/json; charset=utf-8", body, send_body=send_body)

        def _write_response(
            self,
            status: HTTPStatus,
            content_type: str,
            body: bytes,
            *,
            send_body: bool,
        ) -> None:
            self.send_response(int(status))
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; img-src data: http: https:; style-src 'unsafe-inline'; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            )
            self.end_headers()
            if send_body:
                self.wfile.write(body)

    return ReportRequestHandler


def _validate_report_path(path: Path) -> Path:
    report_path = path.expanduser().resolve()
    if not report_path.exists():
        raise ShareError(f"Report file does not exist: {report_path}")
    if not report_path.is_file():
        raise ShareError(f"Report path is not a file: {report_path}")
    if report_path.suffix.lower() not in {"", ".md", ".markdown", ".html", ".htm", ".txt"}:
        raise ShareError("Report must be Markdown, HTML, or plain text.")
    return report_path


def _health_payload(report_path: Path) -> dict[str, Any]:
    stat = report_path.stat()
    return {
        "schema_version": SHARE_SCHEMA_VERSION,
        "status": "ok",
        "report_path": str(report_path),
        "report_bytes": stat.st_size,
        "format": "markdown" if _is_markdown(report_path) else "html",
    }


def _is_markdown(path: Path) -> bool:
    return path.suffix.lower() in {"", ".md", ".markdown", ".txt"}


def _derive_title(text: str, *, fallback: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped.removeprefix("# ").strip() or fallback
        if stripped:
            plain = re.sub(r"<[^>]+>", "", stripped)
            return plain[:80] or fallback
    return fallback


def _html_shell(body: str, *, title: str) -> str:
    escaped_title = html.escape(title, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f8f7f4;
      --text: #202124;
      --muted: #696b70;
      --line: #d8d6cf;
      --panel: #ffffff;
      --accent: #0f766e;
      --code: #f1eee8;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #151515;
        --text: #f3f2ee;
        --muted: #b7b4ad;
        --line: #3d3a34;
        --panel: #1f1f1f;
        --accent: #5eead4;
        --code: #2b2925;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 16px/1.58 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(920px, calc(100% - 32px));
      margin: 0 auto;
      padding: 48px 0 64px;
    }}
    article {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: clamp(20px, 4vw, 44px);
      overflow-wrap: anywhere;
    }}
    h1, h2, h3, h4, h5, h6 {{
      line-height: 1.22;
      margin: 1.8em 0 0.55em;
      letter-spacing: 0;
    }}
    h1 {{ margin-top: 0; font-size: 2rem; }}
    h2 {{ font-size: 1.45rem; border-bottom: 1px solid var(--line); padding-bottom: 0.25rem; }}
    h3 {{ font-size: 1.15rem; }}
    p, ul, ol, blockquote, table, pre {{ margin: 0 0 1rem; }}
    a {{ color: var(--accent); }}
    blockquote {{
      border-left: 3px solid var(--accent);
      color: var(--muted);
      margin-left: 0;
      padding-left: 1rem;
    }}
    code {{
      background: var(--code);
      border-radius: 4px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      font-size: 0.92em;
      padding: 0.12rem 0.28rem;
    }}
    pre {{
      background: var(--code);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow-x: auto;
      padding: 1rem;
    }}
    pre code {{ background: transparent; padding: 0; }}
    table {{
      border-collapse: collapse;
      display: block;
      overflow-x: auto;
      width: 100%;
    }}
    th, td {{
      border: 1px solid var(--line);
      padding: 0.55rem 0.7rem;
      text-align: left;
      vertical-align: top;
    }}
    th {{ background: var(--code); }}
  </style>
</head>
<body>
  <main>
    <article>
{body}
    </article>
  </main>
</body>
</html>
"""


def _markdown_to_html(markdown: str) -> str:
    lines = markdown.splitlines()
    blocks: list[str] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    list_kind: str | None = None
    code_lines: list[str] | None = None
    code_language = ""
    index = 0

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append(f"<p>{_inline_markdown(' '.join(item.strip() for item in paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if list_items and list_kind:
            tag = "ol" if list_kind == "ol" else "ul"
            blocks.append(f"<{tag}>\n" + "\n".join(f"<li>{item}</li>" for item in list_items) + f"\n</{tag}>")
            list_items.clear()
            list_kind = None

    def flush_flow() -> None:
        flush_paragraph()
        flush_list()

    while index < len(lines):
        line = lines[index]
        fence = _FENCE_RE.match(line)
        if code_lines is not None:
            if fence:
                language_attr = (
                    f' class="language-{html.escape(code_language, quote=True)}"' if code_language else ""
                )
                blocks.append(
                    f"<pre><code{language_attr}>{html.escape(chr(10).join(code_lines))}</code></pre>"
                )
                code_lines = None
                code_language = ""
            else:
                code_lines.append(line)
            index += 1
            continue
        if fence:
            flush_flow()
            code_lines = []
            code_language = fence.group(2)
            index += 1
            continue
        if not line.strip():
            flush_flow()
            index += 1
            continue
        if _is_table_start(lines, index):
            flush_flow()
            table_lines = [line, lines[index + 1]]
            index += 2
            while index < len(lines) and "|" in lines[index] and lines[index].strip():
                table_lines.append(lines[index])
                index += 1
            blocks.append(_render_table(table_lines))
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            flush_flow()
            level = len(heading.group(1))
            blocks.append(f"<h{level}>{_inline_markdown(heading.group(2))}</h{level}>")
            index += 1
            continue
        if line.strip() in {"---", "***", "___"}:
            flush_flow()
            blocks.append("<hr>")
            index += 1
            continue
        unordered = _UNORDERED_RE.match(line)
        ordered = _ORDERED_RE.match(line)
        if unordered or ordered:
            flush_paragraph()
            if unordered:
                next_kind = "ul"
                item = unordered.group(1)
            else:
                next_kind = "ol"
                assert ordered is not None
                item = ordered.group(1)
            if list_kind != next_kind:
                flush_list()
                list_kind = next_kind
            list_items.append(_inline_markdown(item))
            index += 1
            continue
        if line.lstrip().startswith(">"):
            flush_flow()
            quote_lines = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quote_lines.append(lines[index].lstrip()[1:].strip())
                index += 1
            blocks.append(f"<blockquote><p>{_inline_markdown(' '.join(quote_lines))}</p></blockquote>")
            continue
        flush_list()
        paragraph.append(line)
        index += 1

    if code_lines is not None:
        language_attr = f' class="language-{html.escape(code_language, quote=True)}"' if code_language else ""
        blocks.append(f"<pre><code{language_attr}>{html.escape(chr(10).join(code_lines))}</code></pre>")
    flush_flow()
    return "\n".join(f"      {block}" for block in blocks)


def _is_table_start(lines: list[str], index: int) -> bool:
    return (
        index + 1 < len(lines) and "|" in lines[index] and bool(_TABLE_SEPARATOR_RE.match(lines[index + 1]))
    )


def _render_table(table_lines: list[str]) -> str:
    rows = [_split_table_row(line) for line in table_lines]
    header = rows[0]
    body_rows = rows[2:]
    cells = "".join(f"<th>{_inline_markdown(cell)}</th>" for cell in header)
    html_lines = ["<table>", f"<thead><tr>{cells}</tr></thead>", "<tbody>"]
    for row in body_rows:
        row_cells = row + [""] * max(0, len(header) - len(row))
        rendered_cells = "".join(f"<td>{_inline_markdown(cell)}</td>" for cell in row_cells[: len(header)])
        html_lines.append(f"<tr>{rendered_cells}</tr>")
    html_lines.append("</tbody></table>")
    return "\n".join(html_lines)


def _split_table_row(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _inline_fragment(fragment: str) -> str:
    return "\n".join(f"      {line}" for line in fragment.splitlines())


def _inline_markdown(text: str) -> str:
    placeholders: list[str] = []

    def code_repl(match: re.Match[str]) -> str:
        placeholders.append(f"<code>{html.escape(match.group(1))}</code>")
        return f"DOCPULLCODE{len(placeholders) - 1}TOKEN"

    escaped = html.escape(_CODE_SPAN_RE.sub(code_repl, text), quote=True)

    def link_repl(match: re.Match[str]) -> str:
        label = match.group(1)
        href = html.unescape(match.group(2))
        safe_href = _safe_href(href)
        return f'<a href="{html.escape(safe_href, quote=True)}" rel="noreferrer">{label}</a>'

    escaped = _LINK_RE.sub(link_repl, escaped)
    escaped = _BOLD_RE.sub(lambda match: f"<strong>{match.group(1) or match.group(2)}</strong>", escaped)
    escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)
    for placeholder_index, replacement in enumerate(placeholders):
        escaped = escaped.replace(f"DOCPULLCODE{placeholder_index}TOKEN", replacement)
    return escaped


def _safe_href(href: str) -> str:
    parsed = urlparse(href.strip())
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https", "mailto"}:
        return "#"
    if not parsed.scheme and not href.startswith(("#", "/")):
        return "#"
    return quote(href, safe="/:#?&=%@+.,;!$'()*[]~")


__all__ = [
    "ReportHTTPServer",
    "ShareError",
    "create_report_server",
    "create_share_parser",
    "render_report_document",
    "report_url",
    "run_share_cli",
]
