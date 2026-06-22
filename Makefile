.PHONY: clean clean-pyc clean-build clean-test help test test-inventory test-all-local benchmark benchmark-quick benchmark-parallel benchmark-compare benchmark-matrix benchmark-raindrop license-year metrics metrics-check metadata-check metadata-sync lint format release-pr release-publish release-publish-replace-tag release-dispatch

PYTHON ?= .venv/bin/python
VERSION_ARG := $(if $(VERSION),--version $(VERSION),)
COPYRIGHT_START_YEAR := 2025
COPYRIGHT_HOLDER := Raintree Technology
COPYRIGHT_FILES := LICENSE mcp/LICENSE
CURRENT_YEAR := $(shell date +%Y)
COPYRIGHT_YEAR_RANGE := $(COPYRIGHT_START_YEAR)
ifneq ($(CURRENT_YEAR),$(COPYRIGHT_START_YEAR))
COPYRIGHT_YEAR_RANGE := $(COPYRIGHT_START_YEAR)-$(CURRENT_YEAR)
endif
COPYRIGHT_NOTICE := Copyright (c) $(COPYRIGHT_YEAR_RANGE) $(COPYRIGHT_HOLDER)

help:
	@echo "clean - remove all build, test, coverage and Python artifacts"
	@echo "clean-build - remove build artifacts"
	@echo "clean-pyc - remove Python file artifacts"
	@echo "clean-test - remove test and coverage artifacts"
	@echo "test - run tests with pytest"
	@echo "test-inventory - print default pytest count, fully gated pytest collection count, Bun MCP count, and coverage"
	@echo "test-all-local - run PR-safe Python, coverage, benchmark, and Bun MCP gates"
	@echo "benchmark - run gated synthetic 10k localhost benchmark"
	@echo "benchmark-quick - run small real-site benchmark without live providers"
	@echo "benchmark-parallel - run real-site benchmark with Parallel under cost guard"
	@echo "benchmark-compare - run real-site benchmark with all configured providers"
	@echo "benchmark-matrix - run provider target-matrix benchmark with all configured providers"
	@echo "benchmark-raindrop - run real-site benchmark with all configured providers and Raindrop tracing"
	@echo "license-year - refresh license copyright years"
	@echo "metrics - refresh METRICS.md and the downloads chart from live APIs"
	@echo "metrics-check - fail if METRICS.md is older than METRICS_MAX_AGE_HOURS"
	@echo "metadata-check - fail if generated release/plugin metadata is stale"
	@echo "metadata-sync - refresh generated release/plugin metadata from source"
	@echo "lint - check style with ruff"
	@echo "format - format code with ruff"
	@echo "release-pr - push current release branch and open a protected-main PR"
	@echo "release-publish - tag merged origin/main and trigger PyPI publish"
	@echo "release-publish-replace-tag - replace an early bad release tag after confirming it did not publish"
	@echo "release-dispatch - manually dispatch PyPI publish from origin/main"

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
	$(PYTHON) -m pytest

test-inventory:
	@echo "Python default pytest:"
	@$(PYTHON) -m pytest --collect-only -q | tail -n 1
	@echo "Python fully gated pytest collection:"
	@DOCPULL_BENCHMARKS=1 DOCPULL_BENCHMARK_10K=1 $(PYTHON) -m pytest --collect-only -q | tail -n 1
	@echo "Bun MCP:"
	@cd mcp && bun test 2>&1 | sed -n 's/^Ran \([0-9][0-9]* tests.*\)/\1/p'
	@echo "Coverage:"
	@COVERAGE_FILE=/tmp/docpull_inventory_coverage $(PYTHON) -m pytest -q --cov=docpull --cov-report=json:/tmp/docpull_inventory_coverage.json >/tmp/docpull_inventory_pytest.log
	@$(PYTHON) -c 'import json; totals=json.load(open("/tmp/docpull_inventory_coverage.json"))["totals"]; print("{}% coverage ({} statements)".format(totals["percent_covered_display"], totals["num_statements"]))'

test-all-local: metadata-check
	COVERAGE_FILE=/tmp/docpull_coverage $(PYTHON) -m pytest -q --durations=25 --cov=docpull --cov-report=term-missing:skip-covered
	DOCPULL_BENCHMARKS=1 $(PYTHON) -m pytest -q tests/benchmarks/test_performance.py
	cd mcp && bun test
	cd mcp && bun run typecheck

benchmark:
	DOCPULL_BENCHMARK_10K=1 $(PYTHON) -m pytest tests/benchmarks/test_10k_pages.py -v -s

benchmark-quick:
	$(PYTHON) -m docpull benchmark quick

benchmark-parallel:
	$(PYTHON) -m docpull benchmark quick --provider parallel --max-estimated-cost 0.05

benchmark-compare:
	$(PYTHON) -m docpull benchmark quick --provider all --max-estimated-cost 0.10

benchmark-matrix:
	$(PYTHON) -m docpull benchmark quick --target-set provider-matrix --provider all \
		--max-pages 8 --max-depth 1 --max-search-results 5 --extract-limit 2 \
		--max-estimated-cost 0.10

benchmark-raindrop:
	$(PYTHON) -m docpull benchmark quick --target-set provider-matrix --provider all --trace raindrop \
		--max-pages 8 --max-depth 1 --max-search-results 5 --extract-limit 2 \
		--max-estimated-cost 0.10

license-year:
	@for file in $(COPYRIGHT_FILES); do \
		perl -0pi -e 's/^Copyright \(c\) [0-9]{4}(?:-(?:[0-9]{4}|present))? \Q$(COPYRIGHT_HOLDER)\E$$/$(COPYRIGHT_NOTICE)/m' "$$file"; \
	done

metrics:
	$(PYTHON) .github/scripts/update_metrics.py
	$(PYTHON) .github/scripts/check_metrics_fresh.py

metrics-check:
	$(PYTHON) .github/scripts/check_metrics_fresh.py

metadata-check:
	$(PYTHON) scripts/sync_release_metadata.py --check

metadata-sync:
	$(PYTHON) scripts/sync_release_metadata.py --write

lint: metadata-check
	$(PYTHON) -m ruff check .

format: license-year metadata-sync
	$(PYTHON) -m ruff format .

release-pr:
	$(PYTHON) scripts/release.py prepare-pr $(VERSION_ARG) --auto-merge

release-publish:
	$(PYTHON) scripts/release.py publish $(VERSION_ARG)

release-publish-replace-tag:
	$(PYTHON) scripts/release.py publish $(VERSION_ARG) --replace-tag

release-dispatch:
	$(PYTHON) scripts/release.py dispatch $(VERSION_ARG)
