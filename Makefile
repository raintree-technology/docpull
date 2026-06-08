.PHONY: clean clean-pyc clean-build clean-test help test benchmark benchmark-quick benchmark-parallel benchmark-compare benchmark-raindrop lint format

PYTHON ?= .venv/bin/python

help:
	@echo "clean - remove all build, test, coverage and Python artifacts"
	@echo "clean-build - remove build artifacts"
	@echo "clean-pyc - remove Python file artifacts"
	@echo "clean-test - remove test and coverage artifacts"
	@echo "test - run tests with pytest"
	@echo "benchmark - run gated synthetic 10k localhost benchmark"
	@echo "benchmark-quick - run small real-site benchmark without live providers"
	@echo "benchmark-parallel - run real-site benchmark with Parallel under cost guard"
	@echo "benchmark-compare - run real-site benchmark with all configured providers"
	@echo "benchmark-raindrop - run real-site benchmark with all configured providers and Raindrop tracing"
	@echo "lint - check style with ruff"
	@echo "format - format code with ruff"

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
	pytest

benchmark:
	DOCPULL_BENCHMARK_10K=1 pytest tests/benchmarks/test_10k_pages.py -v -s

benchmark-quick:
	$(PYTHON) -m docpull benchmark quick

benchmark-parallel:
	$(PYTHON) -m docpull benchmark quick --provider parallel --max-estimated-cost 0.05

benchmark-compare:
	$(PYTHON) -m docpull benchmark quick --provider all --max-estimated-cost 0.10

benchmark-raindrop:
	$(PYTHON) -m docpull benchmark quick --provider all --trace raindrop --max-estimated-cost 0.10

lint:
	ruff check .

format:
	ruff format .
