# Contributing

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/docpull.git
cd docpull
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"
pre-commit install
```

## Workflow

```bash
git checkout -b feature/your-feature
# Make changes, add tests
make pre-commit-check
make typecheck
make test
git commit -m "feat: description"  # Use conventional commits
git push origin feature/your-feature
# Open PR on GitHub
```

## Standards

- Type hints required
- Tests required for new features
- Pre-commit hooks enforce formatting and linting (Ruff) plus type checks (mypy)
- If you touch `web/` or `mcp/`, run that workspace's own install/test/build commands too

## Commit Types

`feat:` `fix:` `docs:` `test:` `refactor:` `chore:`
