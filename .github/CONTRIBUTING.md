# Contributing

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/docpull.git
cd docpull
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pre-commit install
```

## Workflow

```bash
git checkout -b feature/your-feature
# Make changes, add tests
make test-all-local && make lint
git commit -m "feat: description"  # Use conventional commits
git push origin feature/your-feature
# Open PR on GitHub
```

## Testing

Use `make test-inventory` when you need the current test-suite shape. It reports
the default pytest count, the fully gated pytest collection count, the separate
Bun MCP test count, and total Python coverage. Treat those as separate surfaces
rather than a single fixed test total.

Use `make test-all-local` for PR-safe local confidence: default pytest with
Python coverage, lightweight benchmarks, and Bun MCP tests/typecheck. The
10,000-page benchmark stays separate under `make benchmark` because it is
intentionally slow.

## Standards

- Type hints required
- Tests required for new features
- Pre-commit hooks enforce formatting (Black, Ruff)

## Commit Types

`feat:` `fix:` `docs:` `test:` `refactor:` `chore:`
