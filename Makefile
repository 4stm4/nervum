.DEFAULT_GOAL := help
PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: help venv install lint fmt typecheck test cov run openapi clean

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "Targets:\n"} /^[a-zA-Z_-]+:.*?##/ {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

$(VENV)/bin/activate:
	$(PY) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip

venv: $(VENV)/bin/activate ## Create virtualenv

install: venv ## Install project + dev extras
	$(BIN)/pip install -e ".[dev]"

lint: ## Run ruff lint
	$(BIN)/ruff check src tests

fmt: ## Format with ruff
	$(BIN)/ruff format src tests
	$(BIN)/ruff check --fix src tests

typecheck: ## Run mypy
	$(BIN)/mypy

test: ## Run tests
	$(BIN)/pytest

cov: ## Run tests with coverage
	$(BIN)/pytest --cov --cov-report=term-missing

run: ## Run API locally
	$(BIN)/uvicorn sdn_controller.app.main:app --reload --host 0.0.0.0 --port 8080

openapi: ## Export OpenAPI schema
	$(BIN)/python -m sdn_controller.app.openapi_export > openapi/sdn-controller.generated.json

clean: ## Remove caches and build artifacts
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache htmlcov coverage.xml .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
