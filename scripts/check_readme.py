from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"


def main() -> None:
    text = README.read_text(encoding="utf-8")
    required = (
        "<!-- project-record: docpull -->",
        "**Active open-source project",
        "## Install and sync your first source",
        "## Limits and security boundary",
        "## Raintree open-source system",
        "## Project policies",
    )
    missing = [value for value in required if value not in text]
    if missing:
        raise SystemExit(f"README.md is missing required project sections: {missing}")
    if sum(line.startswith("# ") for line in text.splitlines()) != 1:
        raise SystemExit("README.md must contain exactly one H1")
    if text.index("pip install docpull") > text.index("docpull init stripe-docs"):
        raise SystemExit("README.md must install DocPull before the primary command")
    for relative in ("plugin/README.md", "sdk/js/README.md", "docs/cli-recipes.md"):
        if not (ROOT / relative).is_file():
            raise SystemExit(f"documented surface is missing: {relative}")

    for relative in ("plugin/README.md", "sdk/js/README.md"):
        package_readme = (ROOT / relative).read_text(encoding="utf-8")
        for value in ("**Active", "## Install", "Expected result", "boundary"):
            if value.lower() not in package_readme.lower():
                raise SystemExit(f"{relative} is missing package contract text: {value}")
        if sum(line.startswith("# ") for line in package_readme.splitlines()) != 1:
            raise SystemExit(f"{relative} must contain exactly one H1")


if __name__ == "__main__":
    main()
