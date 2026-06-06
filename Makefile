.PHONY: benchmark benchmark-fast benchmark-10k clean clean-pyc clean-build clean-test help
.PHONY: lint lint-check format pre-commit-check typecheck
.PHONY: test test-cov python-security release-gates sync-agent-host-configs sync-claude-plugin

PYTHONPATH ?= src
PYTEST = PYTHONPATH=$(PYTHONPATH) pytest
RUFF = ruff
MYPY = mypy

help:
	@echo "clean - remove all build, test, coverage and Python artifacts"
	@echo "clean-build - remove build artifacts"
	@echo "clean-pyc - remove Python file artifacts"
	@echo "clean-test - remove test and coverage artifacts"
	@echo "test - run tests with pytest"
	@echo "test-cov - run tests with coverage"
	@echo "benchmark - run the default benchmark suite"
	@echo "benchmark-fast - run lightweight performance benchmarks"
	@echo "lint - check style with ruff"
	@echo "lint-check - check style and formatting with ruff"
	@echo "format - format code with ruff"
	@echo "pre-commit-check - run pre-commit across the repo"
	@echo "typecheck - run mypy"
	@echo "python-security - run Python dependency and source security checks"
	@echo "release-gates - run the full Python release gate suite"
	@echo "sync-agent-host-configs - regenerate project-local agent host config files"
	@echo "sync-claude-plugin - regenerate the self-contained Claude plugin bundle"
	@echo "benchmark-10k - run the 10k-page benchmark"

clean: clean-build clean-pyc clean-test

clean-build:
	rm -rf build/
	rm -rf dist/
	rm -rf .eggs/
	find . -path ./.venv -prune -o -name '*.egg-info' -exec rm -rf {} + || true
	find . -path ./.venv -prune -o -name '*.egg' -exec rm -f {} + || true

clean-pyc:
	find . -path ./.venv -prune -o -name '*.pyc' -exec rm -f {} + || true
	find . -path ./.venv -prune -o -name '*.pyo' -exec rm -f {} + || true
	find . -path ./.venv -prune -o -name '*~' -exec rm -f {} + || true
	find . -path ./.venv -prune -o -name '__pycache__' -exec rm -rf {} + || true

clean-test:
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .ruff_cache/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf coverage.xml
	rm -rf test-docs/
	# NOTE: do NOT delete ./docs — it holds CHANGELOG.md and examples/.
	# `docs/` is also the OutputConfig.directory default, but conflating
	# a dev artifact with project source content turned out to be a
	# footgun. Users who run docpull and produce ./docs output should
	# clean it manually or pick a different -o.

test:
	$(PYTEST)

test-cov:
	$(PYTEST) --cov=docpull --cov-report=xml --cov-report=term -q

benchmark:
	$(MAKE) benchmark-10k

benchmark-fast:
	DOCPULL_BENCHMARKS=1 $(PYTEST) tests/benchmarks/test_performance.py -v -s

benchmark-10k:
	DOCPULL_BENCHMARK_10K=1 $(PYTEST) tests/benchmarks/test_10k_pages.py -v -s

lint:
	$(RUFF) check .

lint-check:
	$(RUFF) check .
	$(RUFF) format --check .

format:
	$(RUFF) format .

pre-commit-check:
	pre-commit run --all-files --show-diff-on-failure

typecheck:
	$(MYPY) src

python-security:
	pip-audit
	bandit -q -c pyproject.toml -r src
	$(PYTEST) -q tests/test_security_hardening.py tests/test_discovery.py tests/test_integration.py

release-gates:
	$(MAKE) lint-check
	$(MAKE) typecheck
	$(PYTEST) -q
	pip-audit
	bandit -q -c pyproject.toml -r src

sync-claude-plugin:
	python scripts/sync_claude_plugin.py

sync-agent-host-configs:
	python scripts/sync_agent_host_configs.py
