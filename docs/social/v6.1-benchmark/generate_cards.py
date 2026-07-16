#!/usr/bin/env python3
"""Generate the DocPull 6.1 benchmark LinkedIn carousel."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "assets"
WIDTH = 1080
HEIGHT = 1350

BG = "#0d0e10"
PANEL = "#17191d"
PANEL_2 = "#111316"
BORDER = "#30343a"
STRIPE = "#2b3036"
TEXT = "#f5f4ef"
MUTED = "#a6abb2"
DIM = "#727780"
GREEN = "#177d6d"
GREEN_LIGHT = "#64b9aa"
WARM = "#c8b98a"
RED = "#c98f8f"


def font(size: int, *, bold: bool = False, mono: bool = False) -> ImageFont.FreeTypeFont:
    if mono:
        candidates = [
            "/System/Library/Fonts/Menlo.ttc",
            "/System/Library/Fonts/SFNSMono.ttf",
        ]
    elif bold:
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ]
    else:
        candidates = [
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
        ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    raise RuntimeError("No supported font found")


F_LABEL = font(23, mono=True)
F_SMALL = font(25)
F_BODY = font(31)
F_BODY_BOLD = font(31, bold=True)
F_TITLE = font(67, bold=True)
F_HUGE = font(150, bold=True)
F_METRIC = font(45, bold=True)
F_MONO = font(25, mono=True)


def text_width(draw: ImageDraw.ImageDraw, value: str, fnt: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), value, font=fnt)
    return box[2] - box[0]


def wrap(draw: ImageDraw.ImageDraw, value: str, fnt: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in value.split("\n"):
        if not paragraph:
            lines.append("")
            continue
        current: list[str] = []
        for word in paragraph.split():
            attempt = " ".join([*current, word])
            if current and text_width(draw, attempt, fnt) > max_width:
                lines.append(" ".join(current))
                current = [word]
            else:
                current.append(word)
        if current:
            lines.append(" ".join(current))
    return lines


def draw_wrapped(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    value: str,
    fnt: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    line_height: int,
) -> int:
    x, y = xy
    for line in wrap(draw, value, fnt, max_width):
        draw.text((x, y), line, font=fnt, fill=fill)
        y += line_height
    return y


def rounded_panel(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, fill: str = PANEL) -> None:
    draw.rounded_rectangle(box, radius=22, fill=fill, outline=BORDER, width=2)


def base(page: int, eyebrow: str) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, WIDTH, 12), fill=STRIPE)
    draw.rectangle((0, HEIGHT - 12, WIDTH, HEIGHT), fill=STRIPE)

    logo_path = ROOT.parent.parent / "launch-assets" / "logo-transparent-light-1024.png"
    logo = Image.open(logo_path).convert("RGBA")
    alpha = logo.getchannel("A")
    bbox = alpha.getbbox()
    if bbox:
        logo = logo.crop(bbox)
    logo.thumbnail((62, 62), Image.Resampling.LANCZOS)
    image.paste(logo, (68, 55), logo)
    draw.text((145, 70), "DOCPULL 6.1.0", font=F_LABEL, fill=TEXT)
    draw.text((68, 157), eyebrow.upper(), font=F_LABEL, fill=GREEN_LIGHT)
    draw.text((930, 70), f"{page}/6", font=F_LABEL, fill=DIM)
    return image, draw


def footer(draw: ImageDraw.ImageDraw, text: str = "github.com/raintree-technology/docpull") -> None:
    draw.line((68, 1262, 1012, 1262), fill=BORDER, width=2)
    draw.text((68, 1283), text, font=F_LABEL, fill=DIM)


def save(image: Image.Image, name: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    image.save(OUT / name, optimize=True)


def card_1() -> None:
    image, draw = base(1, "Benchmark result")
    y = draw_wrapped(
        draw,
        (68, 220),
        "Local extraction kept pace with paid APIs.",
        F_TITLE,
        TEXT,
        920,
        79,
    )
    y += 35
    draw.text((68, y), "100.0%", font=F_HUGE, fill=TEXT)
    y += 170
    draw.text((73, y), "strict trial pass", font=F_METRIC, fill=GREEN_LIGHT)
    y += 78
    rounded_panel(draw, (68, y, 1012, y + 210))
    draw.text((102, y + 37), "28 core fixed-URL cases", font=F_BODY_BOLD, fill=TEXT)
    draw.text((102, y + 91), "2 trials per system · all assertions required", font=F_BODY, fill=MUTED)
    draw.text((102, y + 145), "$0 provider spend · local compute excluded", font=F_BODY, fill=MUTED)
    draw.text((68, 1170), "Public development evidence — not claim-grade", font=F_SMALL, fill=WARM)
    footer(draw)
    save(image, "01-cover.png")


def draw_bar(
    draw: ImageDraw.ImageDraw,
    y: int,
    label: str,
    value: float,
    *,
    highlight: bool = False,
) -> None:
    draw.text((68, y), label, font=F_BODY_BOLD if highlight else F_BODY, fill=TEXT)
    value_text = f"{value:.1f}%"
    tw = text_width(draw, value_text, F_BODY_BOLD)
    draw.text((1012 - tw, y), value_text, font=F_BODY_BOLD, fill=TEXT if highlight else MUTED)
    track_y = y + 55
    draw.rounded_rectangle((68, track_y, 1012, track_y + 34), radius=17, fill="#23262b")
    end = 68 + int(944 * value / 100)
    color = GREEN if highlight else "#69717b"
    draw.rounded_rectangle((68, track_y, end, track_y + 34), radius=17, fill=color)


def card_2() -> None:
    image, draw = base(2, "Core extraction")
    y = draw_wrapped(draw, (68, 220), "Strict pass rate", F_TITLE, TEXT, 920, 79)
    draw.text((68, y + 13), "Every predeclared assertion had to pass.", font=F_BODY, fill=MUTED)
    positions = [430, 575, 720, 865]
    values = [
        ("DocPull", 100.0, True),
        ("Parallel", 96.4, False),
        ("Exa Full", 94.6, False),
        ("Tavily", 92.9, False),
    ]
    for pos, (label, value, highlight) in zip(positions, values, strict=True):
        draw_bar(draw, pos, label, value, highlight=highlight)
    rounded_panel(draw, (68, 1055, 1012, 1198), fill=PANEL_2)
    draw.text((102, 1089), "DocPull core operational completion", font=F_BODY, fill=MUTED)
    draw.text((102, 1140), "100.0%", font=F_BODY_BOLD, fill=GREEN_LIGHT)
    draw.text((360, 1140), "Provider spend  $0.00", font=F_BODY_BOLD, fill=TEXT)
    draw.text((68, 1215), "No significant pairwise difference (Holm p = 1.0).", font=F_SMALL, fill=WARM)
    footer(draw)
    save(image, "02-core-results.png")


def card_3() -> None:
    image, draw = base(3, "Boundary conditions")
    y = draw_wrapped(draw, (68, 220), "Core is not the whole benchmark.", F_TITLE, TEXT, 920, 79)
    draw.text((68, y + 10), "Strict pass rate across all 32 cases", font=F_BODY, fill=MUTED)

    rows = [
        ("Parallel", "96.9%"),
        ("Exa Full", "95.3%"),
        ("Tavily", "93.8%"),
        ("DocPull", "87.5%"),
    ]
    start_y = 515
    for index, (label, value) in enumerate(rows):
        row_y = start_y + index * 93
        if label == "DocPull":
            draw.rounded_rectangle((68, row_y - 18, 1012, row_y + 64), radius=14, fill=PANEL)
        draw.text((93, row_y), label, font=F_BODY_BOLD, fill=TEXT)
        tw = text_width(draw, value, F_METRIC)
        draw.text((985 - tw, row_y - 6), value, font=F_METRIC, fill=TEXT)

    rounded_panel(draw, (68, 925, 1012, 1177))
    draw.text((102, 965), "4 boundary cases", font=F_BODY_BOLD, fill=WARM)
    draw_wrapped(
        draw,
        (102, 1022),
        "Managed access and robots policy. Hosted systems completed them; DocPull intentionally stopped.",
        F_BODY,
        MUTED,
        840,
        44,
    )
    draw.text((68, 1203), "Product boundary, not a hidden quality win.", font=F_SMALL, fill=MUTED)
    footer(draw)
    save(image, "03-boundaries.png")


def arrow(draw: ImageDraw.ImageDraw, start: tuple[int, int], end: tuple[int, int]) -> None:
    draw.line((*start, *end), fill=GREEN_LIGHT, width=5)
    x, y = end
    draw.polygon(((x, y), (x - 18, y - 12), (x - 18, y + 12)), fill=GREEN_LIGHT)


def card_4() -> None:
    image, draw = base(4, "PDF isolation")
    y = draw_wrapped(draw, (68, 220), "What changed in 6.1.0", F_TITLE, TEXT, 920, 79)
    draw.text((68, y + 10), "Remote PDFs now cross a constrained worker boundary.", font=F_BODY, fill=MUTED)

    boxes = [
        (68, 485, 285, 640, "PDF", "≤ 50 MiB"),
        (430, 485, 650, 640, "pypdf", "isolated"),
        (795, 485, 1012, 640, "JSON", "validated"),
    ]
    for x1, y1, x2, y2, title, sub in boxes:
        rounded_panel(draw, (x1, y1, x2, y2))
        tw = text_width(draw, title, F_BODY_BOLD)
        draw.text(((x1 + x2 - tw) // 2, y1 + 36), title, font=F_BODY_BOLD, fill=TEXT)
        sw = text_width(draw, sub, F_SMALL)
        draw.text(((x1 + x2 - sw) // 2, y1 + 91), sub, font=F_SMALL, fill=MUTED)
    arrow(draw, (306, 562), (408, 562))
    arrow(draw, (672, 562), (773, 562))

    draw.text((68, 718), "auto fallback", font=F_LABEL, fill=GREEN_LIGHT)
    draw.text((68, 766), "pypdf  →  MarkItDown  →  Unstructured", font=F_MONO, fill=TEXT)

    items = [
        "wall, CPU and address-space limits",
        "credential-stripped worker environment",
        "process-group cleanup on timeout or cancellation",
        "100 MiB parsed-output limit",
        "parser, page count, hash and warning provenance",
    ]
    y = 850
    for item in items:
        draw.rectangle((70, y + 11, 83, y + 24), fill=GREEN)
        draw.text((105, y), item, font=F_BODY, fill=MUTED)
        y += 62
    footer(draw)
    save(image, "04-pdf-isolation.png")


def card_5() -> None:
    image, draw = base(5, "Evidence integrity")
    y = draw_wrapped(draw, (68, 220), "The benchmark was hardened too.", F_TITLE, TEXT, 920, 79)
    draw.text((68, y + 10), "6.1.0 makes inflated or stale claims harder to publish.", font=F_BODY, fill=MUTED)

    items = [
        ("SCORER V4", "Token boundaries; fused words fail."),
        ("REPORT V3", "Trial keys and summaries are recomputed."),
        ("FIXED SCOPE", "Runtime errors cannot move cases out of core."),
        ("SUBJECT ID", "Wheel hash, version and source revision recorded."),
        ("SIGNED EVIDENCE", "Publication hashes, GPG verification and escrow."),
    ]
    y = 470
    for label, detail in items:
        rounded_panel(draw, (68, y, 1012, y + 124), fill=PANEL_2)
        draw.text((96, y + 25), label, font=F_LABEL, fill=GREEN_LIGHT)
        draw.text((96, y + 66), detail, font=F_BODY, fill=TEXT)
        y += 143

    draw.text((68, 1199), "Current v5 bundle status: DATA ONLY", font=F_BODY_BOLD, fill=WARM)
    footer(draw)
    save(image, "05-integrity.png")


def card_6() -> None:
    image, draw = base(6, "Read the evidence")
    y = draw_wrapped(draw, (68, 220), "Results, limitations and release artifacts are public.", F_TITLE, TEXT, 920, 79)

    entries = [
        ("BENCHMARK", "bench/results/manual/…/COMPARISON.md"),
        ("STATUS", "bench/results/STATUS.yaml"),
        ("RELEASE", "github.com/raintree-technology/docpull/releases/tag/v6.1.0"),
        ("PYPI", "pypi.org/project/docpull/6.1.0"),
    ]
    y = 560
    for label, path in entries:
        draw.text((68, y), label, font=F_LABEL, fill=GREEN_LIGHT)
        draw.text((68, y + 42), path, font=F_MONO, fill=TEXT)
        draw.line((68, y + 91, 1012, y + 91), fill=BORDER, width=2)
        y += 143

    rounded_panel(draw, (68, 1120, 1012, 1210))
    draw.text((102, 1149), 'pip install "docpull[pdf]==6.1.0"', font=F_MONO, fill=TEXT)
    footer(draw, "Local-first · open source · browser-free by default")
    save(image, "06-evidence.png")


def contact_sheet() -> None:
    cards = [Image.open(OUT / f"0{index}-{name}.png").convert("RGB") for index, name in [
        (1, "cover"),
        (2, "core-results"),
        (3, "boundaries"),
        (4, "pdf-isolation"),
        (5, "integrity"),
        (6, "evidence"),
    ]]
    thumb_w = 360
    thumb_h = 450
    sheet = Image.new("RGB", (thumb_w * 3, thumb_h * 2), BG)
    for index, card in enumerate(cards):
        thumb = card.resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        sheet.paste(thumb, ((index % 3) * thumb_w, (index // 3) * thumb_h))
    sheet.save(OUT / "contact-sheet.png", optimize=True)


def main() -> None:
    card_1()
    card_2()
    card_3()
    card_4()
    card_5()
    card_6()
    contact_sheet()


if __name__ == "__main__":
    main()
