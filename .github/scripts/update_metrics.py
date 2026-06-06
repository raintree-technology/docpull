"""Refresh METRICS.md from PyPI + GitHub APIs.

Pulled signals:
- PyPI download counts (last day / week / month) via pypistats.org JSON API.
- GitHub repo metadata (stars, forks, watchers, open issues/PRs).
- GitHub traffic (clones, views, referrers, paths) — last 14 days.

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
from datetime import datetime, timezone
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "raintree-technology/docpull")
PKG = os.environ.get("PYPI_PACKAGE", "docpull")
OUTPUT = Path(os.environ.get("METRICS_OUTPUT", "METRICS.md"))
BLANK = ""


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


def fmt(n: int | float) -> str:
    """Format a number with thousands separators."""
    return f"{int(n):,}"


def append_table(lines: list[str], headers: list[str], rows: list[list[str]]) -> None:
    """Append a markdown table to ``lines``."""
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join(["---"] * len(headers)) + "|")
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")


def append_section_with_table(
    lines: list[str],
    title: str,
    headers: list[str],
    rows: list[list[str]],
    *,
    empty_message: str,
) -> None:
    """Append a heading followed by either a table or an empty-state message."""
    lines.extend([title, BLANK])
    if rows:
        append_table(lines, headers, rows)
    else:
        lines.append(empty_message)
    lines.append(BLANK)


def append_table_or_empty(
    lines: list[str],
    headers: list[str],
    rows: list[list[str]],
    *,
    empty_message: str,
) -> None:
    """Append either a table or an empty-state message to the current section."""
    if rows:
        append_table(lines, headers, rows)
    else:
        lines.append(empty_message)
    lines.append(BLANK)


def trim_repo_path(path: str) -> str:
    """Trim the repo prefix from GitHub traffic paths for readability."""
    return path.replace(f"/{REPO}", "") or "/"


def build_snapshot_rows(
    *,
    recent: dict,
    repo: dict,
    open_issues: int,
    open_prs: int,
    clones: dict,
    views: dict,
) -> list[list[str]]:
    """Return the summary table rows for the Snapshot section."""
    return [
        ["PyPI downloads (last 24h)", fmt(recent.get("last_day", 0))],
        ["PyPI downloads (last 7d)", fmt(recent.get("last_week", 0))],
        ["PyPI downloads (last 30d)", fmt(recent.get("last_month", 0))],
        ["GitHub stars", fmt(repo.get("stargazers_count", 0))],
        ["GitHub forks", fmt(repo.get("forks_count", 0))],
        ["GitHub watchers", fmt(repo.get("subscribers_count", 0))],
        ["Open issues", fmt(open_issues)],
        ["Open PRs", fmt(open_prs)],
        ["Repo clones (last 14d)", fmt(clones.get("count", 0))],
        ["Unique cloners (last 14d)", fmt(clones.get("uniques", 0))],
        ["Repo views (last 14d)", fmt(views.get("count", 0))],
        ["Unique visitors (last 14d)", fmt(views.get("uniques", 0))],
    ]


def build_clone_rows(clones: dict) -> list[list[str]]:
    """Return daily clone rows, newest first."""
    return [
        [row.get("timestamp", "")[:10], fmt(row.get("count", 0)), fmt(row.get("uniques", 0))]
        for row in reversed(clones.get("clones", []))
    ]


def build_referrer_rows(referrers: list[dict]) -> list[list[str]]:
    """Return the top referrer rows."""
    return [
        [ref.get("referrer", "?"), fmt(ref.get("count", 0)), fmt(ref.get("uniques", 0))]
        for ref in referrers[:10]
    ]


def build_path_rows(paths: list[dict]) -> list[list[str]]:
    """Return the top path rows."""
    return [
        [f"`{trim_repo_path(path.get('path', '?'))}`", fmt(path.get("count", 0)), fmt(path.get("uniques", 0))]
        for path in paths[:10]
    ]


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
    pypi_blocked = bool(pypi_errors)

    lines: list[str] = []
    push = lines.append
    push(f"# {PKG} metrics")
    push(BLANK)
    push(
        f"_Last updated: {timestamp}. Auto-generated by `.github/workflows/metrics.yml`; "
        "do not edit by hand._"
    )
    push(BLANK)
    push("## Snapshot")
    push(BLANK)
    if pypi_blocked:
        push(
            "> **PyPI download counts unavailable this run.** pypistats.org "
            "returned an error (often a transient rate-limit on shared CI "
            "IPs). Showing the last successful values would be misleading; "
            "the next run should recover automatically."
        )
        push(BLANK)
    append_table(
        lines,
        ["Metric", "Value"],
        build_snapshot_rows(
            recent=recent,
            repo=repo,
            open_issues=open_issues,
            open_prs=open_prs,
            clones=clones,
            views=views,
        ),
    )
    push(BLANK)
    push("## Plugin install proxy: daily clones (last 14d)")
    push(BLANK)
    push(
        f"`/plugin marketplace add {REPO}` is a git clone "
        "under the hood, so daily clone counts approximate plugin installs."
    )
    push(BLANK)
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
        push(BLANK)
    append_table_or_empty(
        lines,
        ["Date", "Clones", "Unique cloners"],
        build_clone_rows(clones),
        empty_message="_No clones recorded in the last 14 days._",
    )
    append_section_with_table(
        lines,
        "## Top referrers (last 14d)",
        ["Source", "Views", "Unique"],
        build_referrer_rows(referrers),
        empty_message="_No referrers recorded in the last 14 days._",
    )
    append_section_with_table(
        lines,
        "## Top paths (last 14d)",
        ["Path", "Views", "Unique"],
        build_path_rows(paths),
        empty_message="_No path traffic recorded in the last 14 days._",
    )
    push("## Drill deeper")
    push(BLANK)
    push(f"- [PyPI page](https://pypi.org/project/{PKG}/)")
    push(f"- [pepy.tech graphs](https://pepy.tech/project/{PKG})")
    push(f"- [pypistats daily history](https://pypistats.org/packages/{PKG})")
    push(f"- [GitHub Insights → Traffic](https://github.com/{REPO}/graphs/traffic)")
    push(f"- [Star history](https://star-history.com/#{REPO}&Date)")
    push(BLANK)

    OUTPUT.write_text("\n".join(lines))
    print(f"Wrote {OUTPUT} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
