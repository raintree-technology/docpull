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
make test && make lint
git commit -m "feat: description"  # Use conventional commits
git push origin feature/your-feature
# Open PR on GitHub
```

## Standards

- Type hints required
- Tests required for new features
- Pre-commit hooks enforce formatting (Black, Ruff)

## Commit Types

`feat:` `fix:` `docs:` `test:` `refactor:` `chore:`
