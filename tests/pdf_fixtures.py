"""Build small, deterministic PDFs for parser tests (no network, no binary fixtures)."""

from __future__ import annotations

from collections.abc import Sequence

Cell = str | Sequence[str]


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _ruled_table(
    x0: float,
    top: float,
    col_widths: Sequence[float],
    row_height: float,
    rows: Sequence[Sequence[Cell]],
) -> list[str]:
    """Content-stream operators for a fully ruled table; cells may hold several lines."""
    ops: list[str] = ["0.5 w"]
    width = sum(col_widths)
    bottom = top - row_height * len(rows)
    for index in range(len(rows) + 1):
        y = top - row_height * index
        ops.append(f"{x0} {y} m {x0 + width} {y} l S")
    x = x0
    for col_width in (0, *col_widths):
        x += col_width
        ops.append(f"{x} {bottom} m {x} {top} l S")
    for row_index, row in enumerate(rows):
        x = x0
        for col_index, cell in enumerate(row):
            lines = [cell] if isinstance(cell, str) else list(cell)
            for line_index, line in enumerate(lines):
                y = top - row_height * row_index - 12 - 11 * line_index
                ops.append(f"BT /F1 9 Tf {x + 4} {y} Td ({_escape(line)}) Tj ET")
            x += col_widths[col_index]
    return ops


def tax_table_pdf() -> bytes:
    """One page: a heading, then two side-by-side bracket tables with wrapped cells."""
    left = [
        ["If Taxable Income Is:", "The Tax Is:"],
        ["Not over $24,800", "10% of the taxable income"],
        [["Over $24,800 but not over", "$100,800"], ["$2,480 plus 12% of the", "excess over $24,800"]],
    ]
    right = [
        ["If Taxable Income Is:", "The Tax Is:"],
        ["Not over $17,700", "10% of the taxable income"],
        [["Over $17,700 but", "not over $67,450"], ["$1,770 plus 12% of", "the excess over $17,700"]],
    ]
    ops = ["BT /F1 11 Tf 50 760 Td (TABLE 1 - Section 1\\(j\\)\\(2\\)\\(A\\) Joint Returns) Tj ET"]
    ops += _ruled_table(40, 740, (120, 140), 32, left)
    ops += _ruled_table(320, 740, (110, 130), 32, right)
    ops.append("BT /F1 10 Tf 50 600 Td (Section 4.02 follows the tables.) Tj ET")
    return build_pdf("\n".join(ops))


def build_pdf(content: str) -> bytes:
    stream = content.encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
