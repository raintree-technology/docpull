# Contributing to docpull

Thank you for your interest in contributing to docpull! This document provides guidelines and instructions for contributing.

## Development Setup

1. **Fork and clone the repository**

```bash
git clone https://github.com/YOUR_USERNAME/docpull.git
cd docpull
```

2. **Set up development environment**

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install with development dependencies
pip install -e ".[dev]"

# Install pre-commit hooks
pre-commit install
```

3. **Verify setup**

```bash
# Run tests
make test

# Run linting
make lint

# Run formatting
make format
```

## Development Workflow

1. **Create a feature branch**

```bash
git checkout -b feature/your-feature-name
# or
git checkout -b fix/your-bug-fix
```

2. **Make your changes**

- Write clear, readable code
- Follow existing code style (enforced by pre-commit hooks)
- Add tests for new functionality
- Update documentation as needed
- Update CHANGELOG.md with your changes

3. **Run tests and linting**

```bash
# Run all tests
make test

# Run linting
make lint

# Format code
make format

# Clean artifacts
make clean
```

4. **Commit your changes**

```bash
git add .
git commit -m "feat: add new feature"
# or
git commit -m "fix: resolve bug in X"
```

Use [Conventional Commits](https://www.conventionalcommits.org/) format:
- `feat:` - New features
- `fix:` - Bug fixes
- `docs:` - Documentation changes
- `test:` - Test additions/changes
- `refactor:` - Code refactoring
- `chore:` - Maintenance tasks
- `ci:` - CI/CD changes

5. **Push and create a pull request**

```bash
git push origin feature/your-feature-name
```

Then open a pull request on GitHub.

## Code Style

- **Python**: Follow PEP 8 (enforced by Ruff and Black)
- **Line length**: 110 characters
- **Type hints**: Required for all functions
- **Docstrings**: Required for public APIs

Pre-commit hooks will automatically:
- Fix trailing whitespace
- Format code with Black and Ruff
- Sort imports
- Check for common issues

## Testing

- Write tests for all new features
- Maintain or improve test coverage
- Run tests with: `make test`
- Check coverage with: `pytest --cov=docpull --cov-report=html`

## Documentation

- Update README.md for user-facing changes
- Update CHANGELOG.md with all changes
- Add docstrings to new functions/classes
- Update TROUBLESHOOTING.md for common issues

## Pull Request Process

1. **Ensure all tests pass**
2. **Update CHANGELOG.md** with your changes
3. **Update documentation** as needed
4. **Fill out the PR template** completely
5. **Wait for review** - maintainers will review your PR
6. **Address feedback** - make requested changes
7. **Merge** - once approved, maintainers will merge

## Release Process

Releases are automated via GitHub Actions:

1. Maintainer triggers release workflow
2. Workflow updates version numbers
3. Workflow updates CHANGELOG.md
4. Workflow creates git tag
5. Workflow creates GitHub release
6. Publish workflow automatically deploys to PyPI

## Adding a New Fetcher

To add support for a new documentation source:

1. Create `docpull/fetchers/yoursite.py`:

```python
from .base import BaseFetcher

class YourSiteFetcher(BaseFetcher):
    """Fetch documentation from YourSite."""

    def __init__(self, output_dir: str = "yoursite-docs"):
        super().__init__(output_dir)
        self.base_url = "https://docs.yoursite.com"

    def fetch_all(self) -> None:
        # Implement fetching logic
        pass
```

2. Register in `docpull/__init__.py`
3. Add tests in `tests/test_yoursite.py`
4. Update README.md with usage example
5. Update CHANGELOG.md

## Getting Help

- Open an issue for bugs or feature requests
- Check existing issues before creating new ones
- Use `docpull --doctor` to diagnose issues
- See TROUBLESHOOTING.md for common problems

## Code of Conduct

- Be respectful and inclusive
- Provide constructive feedback
- Focus on the code, not the person
- Help others learn and grow

Thank you for contributing to docpull!
