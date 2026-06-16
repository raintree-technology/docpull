#!/usr/bin/env python3
"""Guarded release helper for protected-main PyPI releases."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str, capture: bool = False, check: bool = True) -> str:
    proc = subprocess.run(
        args,
        cwd=ROOT,
        check=False,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    if check and proc.returncode:
        if capture and proc.stdout:
            print(proc.stdout, file=sys.stderr, end="")
        raise SystemExit(proc.returncode)
    return proc.stdout.strip() if capture and proc.stdout else ""


def git(*args: str, capture: bool = False, check: bool = True) -> str:
    return run("git", *args, capture=capture, check=check)


def gh(*args: str, capture: bool = False, check: bool = True) -> str:
    return run("gh", *args, capture=capture, check=check)


def project_version_from_text(text: str) -> str:
    return tomllib.loads(text)["project"]["version"]


def local_project_version() -> str:
    return project_version_from_text((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def ref_project_version(ref: str) -> str:
    return project_version_from_text(git("show", f"{ref}:pyproject.toml", capture=True))


def ref_sha(ref: str) -> str:
    return git("rev-parse", "--verify", ref, capture=True)


def current_branch() -> str:
    return git("branch", "--show-current", capture=True)


def ensure_clean() -> None:
    dirty = git("status", "--porcelain", capture=True)
    if dirty:
        raise SystemExit(f"Working tree is not clean:\n{dirty}")


def fetch() -> None:
    git("fetch", "origin", "--prune")


def remote_tag_sha(tag: str) -> str | None:
    output = git("ls-remote", "--tags", "origin", f"refs/tags/{tag}*", capture=True)
    if not output:
        return None
    exact_ref = f"refs/tags/{tag}"
    peeled_ref = f"{exact_ref}^{{}}"
    refs = {}
    for line in output.splitlines():
        sha, ref = line.split(maxsplit=1)
        refs[ref] = sha
    return refs.get(peeled_ref) or refs.get(exact_ref)


def delete_local_tag(tag: str) -> None:
    git("tag", "-d", tag, check=False)


def prepare_pr(args: argparse.Namespace) -> None:
    ensure_clean()
    version = args.version or local_project_version()
    branch = args.branch or f"release/{version}-pr"
    title = args.title or f"chore(release): prepare {version}"
    body = args.body or (
        f"Prepares docpull {version}. After this PR merges, run "
        f"`make release-publish VERSION={version}` from an up-to-date checkout."
    )

    fetch()
    if current_branch() == "main":
        raise SystemExit("Do not prepare a release directly on main. Create a release branch first.")
    if local_project_version() != version:
        raise SystemExit(f"pyproject.toml is {local_project_version()}, not requested version {version}.")

    git("push", "-u", "origin", f"HEAD:{branch}")
    create = gh(
        "pr",
        "create",
        "--base",
        "main",
        "--head",
        branch,
        "--title",
        title,
        "--body",
        body,
        capture=True,
        check=False,
    )
    if create:
        print(create)
    if args.auto_merge:
        gh("pr", "merge", "--squash", "--auto")

    print(f"Release PR branch is {branch}. Do not tag {version} until the PR is merged.")


def publish(args: argparse.Namespace) -> None:
    ensure_clean()
    version = args.version or local_project_version()
    tag = f"v{version}"

    fetch()
    main_sha = ref_sha("origin/main")
    main_version = ref_project_version("origin/main")
    if main_version != version:
        raise SystemExit(f"origin/main has pyproject version {main_version}, not {version}.")

    existing = remote_tag_sha(tag)
    if existing and existing != main_sha:
        if not args.replace_tag:
            raise SystemExit(
                f"Remote {tag} points at {existing[:12]}, but origin/main is {main_sha[:12]}.\n"
                f"Re-run with --replace-tag after confirming {version} was not already published."
            )
        delete_local_tag(tag)
        git("push", "origin", f":refs/tags/{tag}")
        existing = None

    if existing == main_sha:
        print(f"{tag} already points at origin/main {main_sha[:12]}.")
    else:
        delete_local_tag(tag)
        git("tag", tag, main_sha)
        git("push", "origin", tag)
        print(f"Pushed {tag} at origin/main {main_sha[:12]}.")

    print("Watch the publish run with:")
    print("  gh run list --workflow publish.yml --branch", tag, "--limit 5")
    print("  gh run watch <run-id>")


def dispatch(args: argparse.Namespace) -> None:
    ensure_clean()
    version = args.version or local_project_version()
    fetch()
    main_version = ref_project_version("origin/main")
    if main_version != version:
        raise SystemExit(f"origin/main has pyproject version {main_version}, not {version}.")
    gh("workflow", "run", "publish.yml", "--ref", "main", "-f", f"version={version}")
    print("Dispatched Publish to PyPI from main.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    pr = sub.add_parser("prepare-pr", help="Push current release branch and open a main PR.")
    pr.add_argument("--version", help="Expected version; defaults to pyproject.toml")
    pr.add_argument("--branch", help="Remote PR branch; defaults to release/<version>-pr")
    pr.add_argument("--title", help="PR title")
    pr.add_argument("--body", help="PR body")
    pr.add_argument("--auto-merge", action="store_true", help="Enable squash auto-merge")
    pr.set_defaults(func=prepare_pr)

    pub = sub.add_parser("publish", help="Tag merged origin/main and trigger tag-based PyPI publish.")
    pub.add_argument("--version", help="Expected version; defaults to pyproject.toml")
    pub.add_argument("--replace-tag", action="store_true", help="Replace a remote tag that points elsewhere")
    pub.set_defaults(func=publish)

    disp = sub.add_parser("dispatch", help="Manually dispatch PyPI publish from origin/main.")
    disp.add_argument("--version", help="Expected version; defaults to pyproject.toml")
    disp.set_defaults(func=dispatch)

    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
