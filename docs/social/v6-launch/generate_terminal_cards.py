#!/usr/bin/env python3
"""Generate terminal-style social cards for the DocPull v6 launch thread."""

from __future__ import annotations

import textwrap
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "assets"
WIDTH = 1600
HEIGHT = 900

BG = "#0d0e10"
PANEL = "#17191d"
HEADER = "#1d2025"
BORDER = "#32363d"
PILL = "#181b20"
PILL_BORDER = "#3a4048"
STRIPE = "#2b3036"
TEXT = "#f1f0ec"
MUTED = "#a3a7ad"
DIM = "#6c7179"
ACCENT = "#b9c3cf"
ACCENT_DIM = "#8793a0"
SUCCESS = "#d9ddd6"
WARNING = "#c7b98d"
ERROR = "#d2a0a0"


@dataclass(frozen=True)
class Line:
    text: str
    style: str = "normal"


@dataclass(frozen=True)
class Card:
    filename: str
    eyebrow: str
    title: str
    subtitle: str
    terminal_title: str
    lines: tuple[Line, ...]
    footer: str = "docpull v6"


CARDS: tuple[Card, ...] = (
    Card(
        filename="01-context-drift.png",
        eyebrow="Before",
        title="The agent bug nobody sees: context drift.",
        subtitle=(
            "The model sounds confident, but its source context is stale, "
            "uncited, or copied from unknown places."
        ),
        terminal_title="before: agent-context",
        lines=(
            Line("$ ls agent-context", "command"),
            Line("docs-copy.txt          last touched: months ago", "warning"),
            Line("vector-store/          source: unknown", "warning"),
            Line("prompt-notes.md        citations: none", "warning"),
            Line("", "dim"),
            Line("$ ./agent answer 'Did the API change?'", "command"),
            Line("maybe. based on old docs...", "error"),
            Line("", "dim"),
            Line("no lockfile   no diff   no CI gate   no provenance", "error"),
        ),
    ),
    Card(
        filename="02-lockfile-workflow.png",
        eyebrow="After",
        title="Lock agent context like code dependencies.",
        subtitle=(
            "Put docs, specs, packages, repos, feeds, and datasets in "
            "docpull.yaml, then install a reproducible context lockfile."
        ),
        terminal_title="project lockfile",
        lines=(
            Line("$ docpull init agent-context", "command"),
            Line("created docpull.yaml", "success"),
            Line("$ docpull add stripe react openai", "command"),
            Line("resolved 3 aliases to HTTPS sources", "success"),
            Line("$ docpull install", "command"),
            Line("validated dependencies", "success"),
            Line("wrote .docpull/context.lock.json", "success"),
            Line("", "dim"),
            Line("sources, aliases, hashes, run IDs, exports: locked", "info"),
        ),
    ),
    Card(
        filename="03-sync-diff.png",
        eyebrow="Review",
        title="See source changes before agents use them.",
        subtitle=(
            "A sync turns external drift into a diff your team can review instead of a silent prompt change."
        ),
        terminal_title="docpull diff",
        lines=(
            Line("$ docpull sync", "command"),
            Line("synced 3 context dependencies", "success"),
            Line("wrote .docpull/runs/<run_id>", "dim"),
            Line("$ docpull diff", "command"),
            Line("Project diff: +4 -2 ~18 api=2 pricing=1", "info"),
            Line("", "dim"),
            Line("Changed pages:", "normal"),
            Line("- /payments/payment-intents       likely API behavior change", "warning"),
            Line("- /billing/subscriptions          pricing / billing change", "warning"),
            Line("- /webhooks                       likely API behavior change", "warning"),
        ),
    ),
    Card(
        filename="04-v3-pack-contract.png",
        eyebrow="Mechanism",
        title="One contract. Many source types.",
        subtitle=(
            "Every lane writes the same v3 pack shape, so validation, "
            "citations, exports, and CI work the same way."
        ),
        terminal_title="packs/stripe-docs",
        lines=(
            Line("$ docpull pack validate packs/stripe-docs --level eval", "command"),
            Line("raw   corpus.manifest.json", "success"),
            Line("raw   sources.md", "success"),
            Line("raw   acquisition.routes.json", "success"),
            Line("agent context.lock.json", "info"),
            Line("agent coverage.report.json", "info"),
            Line("agent citation.index.json", "info"),
            Line("agent pack.score.json + pack.audit.json", "info"),
            Line("eval  rights.manifest.json", "warning"),
            Line("eval  provenance.graph.json", "warning"),
            Line("eval  basis.ndjson + basis.report.json", "warning"),
            Line("eval  PACK_CARD.md", "warning"),
        ),
    ),
    Card(
        filename="05-context-ci.png",
        eyebrow="Guardrail",
        title="Make bad context fail CI.",
        subtitle=(
            "Treat weak citation coverage or missing eval artifacts like a failed test, not an agent mystery."
        ),
        terminal_title="docpull ci --prepare",
        lines=(
            Line("$ docpull ci --prepare", "command"),
            Line("project_lockfile        pass", "success"),
            Line("pack_score              pass   91", "success"),
            Line("audit_score             pass   94", "success"),
            Line("coverage_confidence     pass   high", "success"),
            Line("citation_coverage       fail   0.72", "error"),
            Line("eval_grade_artifacts    pass", "success"),
            Line("rights_status           warn   unknown", "warning"),
            Line("", "dim"),
            Line("Context CI failed: 4 pass, 1 warn, 1 fail", "error"),
            Line("see context-ci.report.json", "dim"),
        ),
    ),
    Card(
        filename="06-export-agent-context.png",
        eyebrow="Ship",
        title="Send the same pack everywhere agents work.",
        subtitle=(
            "Export the locked, cited context to Cursor, Codex, OpenAI, LangChain, MCP, and RAG pipelines."
        ),
        terminal_title="agent exports",
        lines=(
            Line("$ docpull export context-pack --target cursor", "command"),
            Line("wrote context.md + citations.json", "success"),
            Line("wrote Cursor rule references", "success"),
            Line("$ docpull export context-pack --target codex", "command"),
            Line("wrote Codex skill references", "success"),
            Line("$ docpull export context-pack --target openai", "command"),
            Line("wrote openai-vector.jsonl", "success"),
            Line("", "dim"),
            Line("export recorded in .docpull/context.lock.json", "info"),
        ),
        footer="pip install docpull",
    ),
)


def font(path: str, size: int, fallback: str = "") -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(path),
        Path(fallback) if fallback else None,
        Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
        Path("/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        Path("/Library/Fonts/Arial.ttf"),
    ]
    for candidate in candidates:
        if candidate and candidate.exists():
            try:
                return ImageFont.truetype(str(candidate), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


REG = font("/System/Library/Fonts/Supplemental/Arial.ttf", 34)
BOLD = font("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 62)
SMALL = font("/System/Library/Fonts/Supplemental/Arial.ttf", 25)
MONO = font("/System/Library/Fonts/Menlo.ttc", 28)
MONO_SMALL = font("/System/Library/Fonts/Menlo.ttc", 24)


def text_size(draw: ImageDraw.ImageDraw, value: str, fnt: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), value, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def wrap(draw: ImageDraw.ImageDraw, value: str, fnt: ImageFont.ImageFont, max_width: int) -> list[str]:
    words = value.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        attempt = " ".join([*current, word])
        if text_size(draw, attempt, fnt)[0] <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def style_color(style: str) -> str:
    return {
        "command": ACCENT,
        "success": SUCCESS,
        "warning": WARNING,
        "error": ERROR,
        "info": ACCENT_DIM,
        "dim": DIM,
        "normal": TEXT,
    }.get(style, TEXT)


def draw_card(card: Card) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)

    # Background bands.
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=BG)
    draw.rectangle((0, 0, WIDTH, 12), fill=STRIPE)
    draw.rectangle((0, HEIGHT - 12, WIDTH, HEIGHT), fill=STRIPE)

    left = 86
    top = 62
    max_text = 640

    draw.text((left, top), card.eyebrow.upper(), font=MONO_SMALL, fill=ACCENT)
    y = top + 54
    for line in wrap(draw, card.title, BOLD, max_text):
        draw.text((left, y), line, font=BOLD, fill=TEXT)
        y += 72
    y += 18
    for line in wrap(draw, card.subtitle, REG, max_text):
        draw.text((left, y), line, font=REG, fill=MUTED)
        y += 46

    # Footer pill.
    footer_w, footer_h = text_size(draw, card.footer, MONO_SMALL)
    draw.rounded_rectangle(
        (left, HEIGHT - 106, left + footer_w + 42, HEIGHT - 58),
        radius=12,
        fill=PILL,
        outline=PILL_BORDER,
        width=2,
    )
    draw.text((left + 21, HEIGHT - 96), card.footer, font=MONO_SMALL, fill=TEXT)

    # Terminal panel.
    panel_x = 780
    panel_y = 86
    panel_w = 730
    panel_h = 720
    draw.rounded_rectangle(
        (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
        radius=24,
        fill=PANEL,
        outline=BORDER,
        width=3,
    )
    draw.rounded_rectangle(
        (panel_x, panel_y, panel_x + panel_w, panel_y + 70),
        radius=24,
        fill=HEADER,
    )
    draw.rectangle((panel_x, panel_y + 46, panel_x + panel_w, panel_y + 70), fill=HEADER)
    for i, color in enumerate(("#7c828b", "#a1a6ad", "#c8ccd0")):
        draw.ellipse((panel_x + 26 + i * 34, panel_y + 26, panel_x + 44 + i * 34, panel_y + 44), fill=color)
    draw.text((panel_x + 138, panel_y + 22), card.terminal_title, font=MONO_SMALL, fill=MUTED)

    x = panel_x + 34
    y = panel_y + 104
    line_h = 40
    for item in card.lines:
        if not item.text:
            y += 22
            continue
        max_chars = 38
        wrapped = textwrap.wrap(item.text, width=max_chars, subsequent_indent="  ") or [item.text]
        for line in wrapped:
            draw.text((x, y), line, font=MONO, fill=style_color(item.style))
            y += line_h
        if y > panel_y + panel_h - 54:
            break

    # Tiny source label.
    draw.text(
        (panel_x + 34, panel_y + panel_h + 22),
        "github.com/raintree-technology/docpull",
        font=MONO_SMALL,
        fill=DIM,
    )

    OUT.mkdir(parents=True, exist_ok=True)
    image.save(OUT / card.filename, optimize=True)


def main() -> None:
    for card in CARDS:
        draw_card(card)
    print(f"wrote {len(CARDS)} cards to {OUT}")


if __name__ == "__main__":
    main()
