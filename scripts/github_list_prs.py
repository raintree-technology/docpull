#!/usr/bin/env python3
"""Prepare GitHub awesome-list PRs from local recipes.

The script is dry-run by default. It only writes to external repositories when
called with --prepare, and it only pushes/opens PRs when --push/--create-pr are
also passed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path

# Bandit B404 is suppressed because this script uses fixed executables and
# direct argument vectors without a shell.

DEFAULT_WORKDIR = Path(".tmp/github-list-prs")


@dataclass(frozen=True)
class Change:
    file: str
    mode: str
    entry: str
    section: str | None = None


@dataclass(frozen=True)
class Recipe:
    id: str
    ready: bool
    upstream: str
    fork_name: str | None
    base_branch: str
    branch: str
    commit_message: str
    title: str
    body: str
    changes: tuple[Change, ...]


def main() -> int:
    args = parse_args()
    recipes = load_recipes(args.recipes)
    selected = [recipe for recipe in recipes if args.id in {None, recipe.id}]
    if args.ready_only:
        selected = [recipe for recipe in selected if recipe.ready]
    if not selected:
        print("No recipes selected.", file=sys.stderr)
        return 1

    for recipe in selected:
        print_recipe_summary(recipe)
        if args.dry_run and not args.prepare:
            continue
        prepare_recipe(recipe, args)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipes", type=Path, required=True, help="Path to recipe JSON")
    parser.add_argument("--id", help="Only process one recipe id")
    parser.add_argument("--workdir", type=Path, default=DEFAULT_WORKDIR)
    parser.add_argument("--ready-only", action="store_true", default=True)
    parser.add_argument("--include-deferred", dest="ready_only", action="store_false")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--prepare", action="store_true", help="Clone/fork, edit, and commit locally")
    parser.add_argument("--push", action="store_true", help="Push branch to zacharyr0th fork")
    parser.add_argument("--create-pr", action="store_true", help="Open PR with gh")
    parser.add_argument("--force-reclone", action="store_true", help="Remove existing local clone first")
    return parser.parse_args()


def load_recipes(path: Path) -> list[Recipe]:
    data = json.loads(path.read_text(encoding="utf-8"))
    recipes: list[Recipe] = []
    for raw in data:
        recipes.append(
            Recipe(
                id=str(raw["id"]),
                ready=bool(raw.get("ready")),
                upstream=str(raw["upstream"]),
                fork_name=raw.get("fork_name"),
                base_branch=str(raw["base_branch"]),
                branch=str(raw["branch"]),
                commit_message=str(raw["commit_message"]),
                title=str(raw["title"]),
                body=str(raw["body"]),
                changes=tuple(
                    Change(
                        file=str(change["file"]),
                        mode=str(change["mode"]),
                        entry=str(change["entry"]),
                        section=change.get("section"),
                    )
                    for change in raw.get("changes", [])
                ),
            )
        )
    return recipes


def print_recipe_summary(recipe: Recipe) -> None:
    status = "ready" if recipe.ready else "deferred"
    print(f"\n[{recipe.id}] {recipe.upstream} ({status})")
    print(f"  branch: {recipe.branch}")
    print(f"  title: {recipe.title}")
    for change in recipe.changes:
        target = f"{change.file} :: {change.section}" if change.section else change.file
        print(f"  change: {change.mode} -> {target}")
        print(f"    {change.entry.splitlines()[0]}")


def prepare_recipe(recipe: Recipe, args: argparse.Namespace) -> None:
    if not recipe.ready:
        raise SystemExit(f"Recipe {recipe.id} is deferred; pass a ready recipe.")
    ensure_tools()

    _, upstream_name = recipe.upstream.split("/", 1)
    fork_name = recipe.fork_name or upstream_name
    repo_dir = args.workdir / fork_name
    args.workdir.mkdir(parents=True, exist_ok=True)

    if args.force_reclone and repo_dir.exists():
        shutil.rmtree(repo_dir)

    ensure_fork(recipe.upstream, fork_name)
    ensure_clone(repo_dir, recipe.upstream, fork_name)
    run(["git", "fetch", "upstream", recipe.base_branch], cwd=repo_dir)
    run(["git", "checkout", "-B", recipe.branch, f"upstream/{recipe.base_branch}"], cwd=repo_dir)

    for change in recipe.changes:
        apply_change(repo_dir / change.file, change)

    run(["git", "diff", "--", *sorted({change.file for change in recipe.changes})], cwd=repo_dir, check=False)
    run(["git", "add", *sorted({change.file for change in recipe.changes})], cwd=repo_dir)

    diff_check = run(["git", "diff", "--cached", "--quiet"], cwd=repo_dir, check=False, capture=True)
    if diff_check.returncode == 0:
        print("  no staged changes; skipping commit")
    else:
        run(["git", "commit", "-m", recipe.commit_message], cwd=repo_dir)

    if args.push or args.create_pr:
        run(["git", "push", "-u", "origin", recipe.branch, "--force-with-lease"], cwd=repo_dir)
    if args.create_pr:
        body_path = (repo_dir / ".docpull-pr-body.md").resolve()
        body_path.write_text(recipe.body + "\n", encoding="utf-8")
        run(
            [
                "gh",
                "pr",
                "create",
                "--repo",
                recipe.upstream,
                "--base",
                recipe.base_branch,
                "--head",
                f"zacharyr0th:{recipe.branch}",
                "--title",
                recipe.title,
                "--body-file",
                str(body_path),
            ],
            cwd=repo_dir,
        )


def ensure_tools() -> None:
    for tool in ("git", "gh"):
        if shutil.which(tool) is None:
            raise SystemExit(f"Missing required tool: {tool}")


def ensure_fork(upstream: str, fork_name: str) -> None:
    fork_repo = f"zacharyr0th/{fork_name}"
    result = run(["gh", "repo", "view", fork_repo], check=False, capture=True)
    if result.returncode == 0:
        return
    run(["gh", "repo", "fork", upstream, "--clone=false", "--fork-name", fork_name])


def ensure_clone(repo_dir: Path, upstream: str, name: str) -> None:
    if not repo_dir.exists():
        run(["git", "clone", f"https://github.com/zacharyr0th/{name}.git", str(repo_dir)])
    remotes = run(["git", "remote"], cwd=repo_dir, capture=True).stdout.splitlines()
    if "upstream" not in remotes:
        run(["git", "remote", "add", "upstream", f"https://github.com/{upstream}.git"], cwd=repo_dir)
    else:
        run(["git", "remote", "set-url", "upstream", f"https://github.com/{upstream}.git"], cwd=repo_dir)
    run(["git", "remote", "set-url", "origin", f"https://github.com/zacharyr0th/{name}.git"], cwd=repo_dir)


def apply_change(path: Path, change: Change) -> None:
    text = path.read_text(encoding="utf-8")
    if "docpull" in text.lower():
        print(f"  {path}: docpull already present; leaving unchanged")
        return
    if change.mode == "append_markdown_section":
        if not change.section:
            raise ValueError("append_markdown_section requires section")
        text = append_markdown_section(text, change.section, change.entry)
    elif change.mode == "append_yaml_project":
        text = append_yaml_project(text, change.entry)
    else:
        raise ValueError(f"Unsupported change mode: {change.mode}")
    path.write_text(text, encoding="utf-8")


def append_markdown_section(text: str, section: str, entry: str) -> str:
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == section)
    except StopIteration as exc:
        raise ValueError(f"Section not found: {section}") from exc

    heading_level = len(section) - len(section.lstrip("#"))
    insert_at = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            if level <= heading_level:
                insert_at = i
                break

    while insert_at > start and not lines[insert_at - 1].strip():
        insert_at -= 1
    if insert_at > start and lines[insert_at - 1].strip().lower() == "<br />":
        insert_at -= 1
        while insert_at > start and not lines[insert_at - 1].strip():
            insert_at -= 1
    lines.insert(insert_at, entry)
    return "\n".join(lines) + "\n"


def append_yaml_project(text: str, entry: str) -> str:
    stripped = text.rstrip()
    return stripped + "\n" + entry.rstrip() + "\n"


def run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print("  $ " + " ".join(cmd))
    # Arguments are passed directly and shell execution is never enabled.
    result = subprocess.run(  # nosec B603
        cmd,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if check and result.returncode != 0:
        if capture:
            print(result.stdout, end="")
            print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
