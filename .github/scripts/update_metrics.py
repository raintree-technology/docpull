"""Refresh METRICS.md and download charts from PyPI + GitHub APIs.

Pulled signals:
- PyPI download counts (last day / week / month) via pypistats.org JSON API.
- GitHub repo metadata (stars, forks, watchers, open issues/PRs).
- GitHub traffic (clones, views, referrers, paths) — last 14 days.
- PyPI daily download chart via pepy.tech.

Plugin install proxy: `/plugin marketplace add <owner>/<repo>` is a git
clone under the hood, so daily clone counts approximate plugin installs.

Stdlib only — no extra deps to keep CI fast and the supply chain small.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "raintree-technology/docpull")
PKG = os.environ.get("PYPI_PACKAGE", "docpull")
OUTPUT = Path(os.environ.get("METRICS_OUTPUT", "METRICS.md"))
DOWNLOAD_CHART_OUTPUT = Path(os.environ.get("DOWNLOAD_CHART_OUTPUT", "docs/downloads-history.svg"))


def gh(path: str) -> dict | list:
    """GET against the GitHub REST API via the gh CLI.

    Locally this uses the user's gh auth. In CI, set GH_TOKEN (or
    GITHUB_TOKEN) on the step's env and gh picks it up automatically.
    ``path`` is relative to the configured repo, e.g. ``/traffic/clones``.
    """
    full = f"repos/{REPO}{path}"
    out = subprocess.check_output(
        ["gh", "api", "-H", "X-GitHub-Api-Version: 2022-11-28", full],
        text=True,
    )
    return json.loads(out)


def gh_search_count(q: str) -> int:
    """Use the search API to get a count (total_count is cheap; per_page=1)."""
    out = subprocess.check_output(
        ["gh", "api", "-X", "GET", "search/issues", "-f", f"q={q}", "-F", "per_page=1"],
        text=True,
    )
    return int(json.loads(out).get("total_count", 0))


def pypistats(path: str) -> dict:
    """GET against the pypistats.org JSON API (no auth needed).

    Sets a descriptive User-Agent so the request is identifiable and less
    likely to hit shared-IP rate limits on github-actions runners.
    """
    url = f"https://pypistats.org/api/packages/{PKG}{path}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"{REPO}-metrics-workflow (+https://github.com/{REPO})"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def pepy_project() -> dict:
    """GET project download history from Pepy.

    Pepy exposes all-time totals plus a per-version daily breakdown for the
    last roughly 90 days. We aggregate that version map into daily totals for
    the README chart.
    """
    url = f"https://pepy.tech/api/v2/projects/{PKG}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": f"{REPO}-metrics-workflow (+https://github.com/{REPO})"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def fmt(n: int | float) -> str:
    """Format a number with thousands separators."""
    return f"{int(n):,}"


def short_fmt(n: int | float) -> str:
    """Compact label for chart axes."""
    n = int(n)
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if abs(n) >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def safe_get(fn, default, *, on_error: list[str] | None = None):
    """Best-effort wrapper — never let a transient API hiccup blank METRICS.md.

    If ``on_error`` is provided, append the stringified error so the caller
    can distinguish "API failed" from "API returned empty data".
    """
    try:
        return fn()
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        subprocess.CalledProcessError,
        KeyError,
        ValueError,
    ) as err:
        msg = str(err)
        print(f"warn: {msg}", file=sys.stderr)
        if on_error is not None:
            on_error.append(msg)
        return default


def download_series(pepy: dict, *, days: int = 90) -> list[tuple[date, int]]:
    """Aggregate Pepy's per-version daily downloads into one daily series."""
    raw = pepy.get("downloads", {})
    totals: dict[date, int] = {}
    for day, versions in raw.items():
        try:
            day_key = date.fromisoformat(day)
        except ValueError:
            continue
        if isinstance(versions, dict):
            totals[day_key] = sum(int(v or 0) for v in versions.values())

    end = max(totals) if totals else datetime.now(timezone.utc).date()
    start = end - timedelta(days=days - 1)
    return [(start + timedelta(days=i), totals.get(start + timedelta(days=i), 0)) for i in range(days)]


def cumulative_download_series(
    daily: list[tuple[date, int]], *, total_downloads: int
) -> list[tuple[date, int]]:
    """Convert a daily window into an estimated cumulative all-time series."""
    window_total = sum(value for _, value in daily)
    running = max(0, total_downloads - window_total)
    cumulative: list[tuple[date, int]] = []
    for day, value in daily:
        running += value
        cumulative.append((day, running))
    return cumulative


def render_download_chart(daily_series: list[tuple[date, int]], *, total_downloads: int) -> str:
    """Render a dependency-free SVG chart for README embedding."""
    series = cumulative_download_series(daily_series, total_downloads=total_downloads)
    width = 920
    height = 360
    left = 70
    right = 28
    top = 72
    bottom = 64
    chart_w = width - left - right
    chart_h = height - top - bottom
    max_y = max([value for _, value in series] + [total_downloads, 1])
    # Give the line a little headroom and round the top tick to a clean value.
    top_tick = max(10, int(((max_y * 1.18) + 9) // 10 * 10))

    def x_for(i: int) -> float:
        if len(series) <= 1:
            return left
        return left + (i / (len(series) - 1)) * chart_w

    def y_for(value: int | float) -> float:
        return top + chart_h - (float(value) / top_tick) * chart_h

    points = [(x_for(i), y_for(value)) for i, (_, value) in enumerate(series)]
    line_points = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    area_points = (
        f"{left:.1f},{top + chart_h:.1f} " + line_points + f" {left + chart_w:.1f},{top + chart_h:.1f}"
    )

    grid_lines: list[str] = []
    axis_labels: list[str] = []
    for tick in range(5):
        value = top_tick * tick / 4
        y = y_for(value)
        grid_lines.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{left + chart_w}" y2="{y:.1f}" '
            'stroke="#e5e7eb" stroke-width="1" />'
        )
        axis_labels.append(
            f'<text x="{left - 14}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="12" fill="#6b7280">{escape(short_fmt(value))}</text>'
        )

    month_labels: list[str] = []
    seen_months: set[tuple[int, int]] = set()
    for i, (day, _) in enumerate(series):
        key = (day.year, day.month)
        if key in seen_months:
            continue
        seen_months.add(key)
        x = x_for(i)
        month_labels.append(
            f'<text x="{x:.1f}" y="{height - 26}" text-anchor="middle" '
            f'font-size="12" fill="#6b7280">{escape(day.strftime("%b %d"))}</text>'
        )

    start_label = series[0][0].strftime("%b %-d, %Y") if series else ""
    end_label = series[-1][0].strftime("%b %-d, %Y") if series else ""
    latest_value = series[-1][1] if series else total_downloads
    total_window = sum(value for _, value in daily_series)
    subtitle = (
        f"Cumulative downloads, last {len(series)} days · +{fmt(total_window)} in window · "
        f"{fmt(total_downloads)} all time"
    )
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    svg_lines = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">'
        ),
        f'  <title id="title">{escape(PKG)} cumulative PyPI download history</title>',
        (
            f'  <desc id="desc">Cumulative PyPI downloads from Pepy for {escape(PKG)} '
            f"between {escape(start_label)} and {escape(end_label)}.</desc>"
        ),
        f'  <rect width="{width}" height="{height}" rx="16" fill="#ffffff" />',
        (
            f'  <text x="{left}" y="34" font-size="22" font-weight="700" '
            f'fill="#111827">{escape(PKG)} cumulative PyPI downloads</text>'
        ),
        f'  <text x="{left}" y="56" font-size="13" fill="#6b7280">{escape(subtitle)}</text>',
        (
            f'  <text x="{width - right}" y="34" text-anchor="end" font-size="20" '
            f'font-weight="700" fill="#2563eb">{escape(short_fmt(latest_value))}</text>'
        ),
        f'  <text x="{width - right}" y="55" text-anchor="end" font-size="12" fill="#6b7280">all time</text>',
        *grid_lines,
        *axis_labels,
        (
            f'  <line x1="{left}" y1="{top + chart_h}" x2="{left + chart_w}" '
            f'y2="{top + chart_h}" stroke="#d1d5db" stroke-width="1.5" />'
        ),
        f'  <polygon points="{area_points}" fill="#dbeafe" opacity="0.85" />',
        (
            f'  <polyline points="{line_points}" fill="none" stroke="#2563eb" '
            'stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />'
        ),
        *month_labels,
        (f'  <text x="{left}" y="{height - 10}" font-size="11" fill="#9ca3af">Source: pepy.tech API</text>'),
        (
            f'  <text x="{width - right}" y="{height - 10}" text-anchor="end" '
            f'font-size="11" fill="#9ca3af">Generated {escape(generated_at)}</text>'
        ),
        "</svg>",
        "",
    ]
    return "\n".join(svg_lines)


def write_download_chart(pepy: dict) -> None:
    series = download_series(pepy)
    total_downloads = int(pepy.get("total_downloads", 0) or 0)
    DOWNLOAD_CHART_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    DOWNLOAD_CHART_OUTPUT.write_text(render_download_chart(series, total_downloads=total_downloads))


def main() -> int:
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d %H:%M UTC")

    repo = safe_get(lambda: gh(""), {})

    # Traffic endpoints require Administration: read — collect errors so we
    # can show a clear note in METRICS.md if the token is under-scoped.
    traffic_errors: list[str] = []
    clones = safe_get(
        lambda: gh("/traffic/clones"),
        {"count": 0, "uniques": 0, "clones": []},
        on_error=traffic_errors,
    )
    views = safe_get(
        lambda: gh("/traffic/views"),
        {"count": 0, "uniques": 0, "views": []},
        on_error=traffic_errors,
    )
    referrers = safe_get(lambda: gh("/traffic/popular/referrers"), [], on_error=traffic_errors)
    paths = safe_get(lambda: gh("/traffic/popular/paths"), [], on_error=traffic_errors)
    traffic_blocked = bool(traffic_errors)

    open_issues = safe_get(lambda: gh_search_count(f"repo:{REPO} is:open is:issue"), 0)
    open_prs = safe_get(lambda: gh_search_count(f"repo:{REPO} is:open is:pr"), 0)

    pypi_errors: list[str] = []
    recent = safe_get(lambda: pypistats("/recent")["data"], {}, on_error=pypi_errors)
    pepy = safe_get(lambda: pepy_project(), {}, on_error=pypi_errors)
    pypi_blocked = bool(pypi_errors)
    if pepy:
        write_download_chart(pepy)

    stars = repo.get("stargazers_count", 0)
    forks = repo.get("forks_count", 0)
    watchers = repo.get("subscribers_count", 0)

    last_day = recent.get("last_day", 0)
    last_week = recent.get("last_week", 0)
    last_month = recent.get("last_month", 0)

    lines: list[str] = []
    push = lines.append
    push(f"# {PKG} metrics")
    push("")
    push(
        f"_Last updated: {timestamp}. Auto-generated by `.github/workflows/metrics.yml`; "
        "do not edit by hand._"
    )
    push("")
    push("## Snapshot")
    push("")
    if pypi_blocked:
        push(
            "> **PyPI download counts unavailable this run.** pypistats.org "
            "returned an error (often a transient rate-limit on shared CI "
            "IPs). Showing the last successful values would be misleading; "
            "the next run should recover automatically."
        )
        push("")
    push("| Metric | Value |")
    push("|---|---|")
    push(f"| PyPI downloads (last 24h) | {fmt(last_day)} |")
    push(f"| PyPI downloads (last 7d) | {fmt(last_week)} |")
    push(f"| PyPI downloads (last 30d) | {fmt(last_month)} |")
    push(f"| GitHub stars | {fmt(stars)} |")
    push(f"| GitHub forks | {fmt(forks)} |")
    push(f"| GitHub watchers | {fmt(watchers)} |")
    push(f"| Open issues | {fmt(open_issues)} |")
    push(f"| Open PRs | {fmt(open_prs)} |")
    push(f"| Repo clones (last 14d) | {fmt(clones.get('count', 0))} |")
    push(f"| Unique cloners (last 14d) | {fmt(clones.get('uniques', 0))} |")
    push(f"| Repo views (last 14d) | {fmt(views.get('count', 0))} |")
    push(f"| Unique visitors (last 14d) | {fmt(views.get('uniques', 0))} |")
    push("")
    push("## Plugin install proxy: daily clones (last 14d)")
    push("")
    push(
        f"`/plugin marketplace add {REPO}` is a git clone "
        "under the hood, so daily clone counts approximate plugin installs."
    )
    push("")
    if traffic_blocked:
        push(
            "> **Traffic data unavailable.** The workflow's token is missing "
            "`Administration: read` permission. Create a fine-grained PAT "
            "scoped to this repo with `Administration: read` + `Metadata: "
            "read`, save as repo secret `METRICS_TOKEN`, and re-run the "
            "workflow. See the comment header in "
            "[`.github/workflows/metrics.yml`](.github/workflows/metrics.yml) "
            "for full setup."
        )
        push("")
    daily = clones.get("clones", [])
    if daily:
        push("| Date | Clones | Unique cloners |")
        push("|---|---|---|")
        # GitHub returns oldest-first; show newest-first for readability.
        for row in reversed(daily):
            ts = row.get("timestamp", "")[:10]
            push(f"| {ts} | {fmt(row.get('count', 0))} | {fmt(row.get('uniques', 0))} |")
    else:
        push("_No clones recorded in the last 14 days._")
    push("")
    push("## Top referrers (last 14d)")
    push("")
    if referrers:
        push("| Source | Views | Unique |")
        push("|---|---|---|")
        for ref in referrers[:10]:
            push(
                f"| {ref.get('referrer', '?')} | {fmt(ref.get('count', 0))} | {fmt(ref.get('uniques', 0))} |"
            )
    else:
        push("_No referrers recorded in the last 14 days._")
    push("")
    push("## Top paths (last 14d)")
    push("")
    if paths:
        push("| Path | Views | Unique |")
        push("|---|---|---|")
        for p in paths[:10]:
            label = p.get("path", "?")
            # Paths are full URLs; trim the repo prefix for readability.
            label = label.replace(f"/{REPO}", "") or "/"
            push(f"| `{label}` | {fmt(p.get('count', 0))} | {fmt(p.get('uniques', 0))} |")
    else:
        push("_No path traffic recorded in the last 14 days._")
    push("")
    push("## Drill deeper")
    push("")
    push(f"- [PyPI page](https://pypi.org/project/{PKG}/)")
    push(f"- [pepy.tech graphs](https://pepy.tech/project/{PKG})")
    push(f"- [pypistats daily history](https://pypistats.org/packages/{PKG})")
    push(f"- [GitHub Insights → Traffic](https://github.com/{REPO}/graphs/traffic)")
    push(f"- [Star history](https://star-history.com/#{REPO}&Date)")
    push("")

    OUTPUT.write_text("\n".join(lines))
    print(f"Wrote {OUTPUT} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
