.PHONY: clean clean-pyc clean-build clean-test help

help:
	@echo "clean - remove all build, test, coverage and Python artifacts"
	@echo "clean-build - remove build artifacts"
	@echo "clean-pyc - remove Python file artifacts"
	@echo "clean-test - remove test and coverage artifacts"
	@echo "test - run tests with pytest"
	@echo "lint - check style with ruff"
	@echo "format - format code with black"

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
	rm -rf docs/
	rm -rf test-docs/

test:
	pytest

lint:
	ruff check .

format:
	black .
