.PHONY: install dev lint format typecheck test test-unit test-integration cov clean

install:
	uv sync

dev:
	uv sync --extra dev

lint:
	uv run ruff check src/ tests/

format:
	uv run ruff format src/ tests/
	uv run ruff check --fix src/ tests/

typecheck:
	uv run mypy src/

test:
	uv run pytest

test-unit:
	uv run pytest tests/unit -m unit

test-integration:
	uv run pytest tests/integration -m integration

cov:
	uv run pytest --cov --cov-report=html
	@echo "Coverage report: htmlcov/index.html"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage dist build *.egg-info
