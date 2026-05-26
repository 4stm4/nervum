.DEFAULT_GOAL := help
PY ?= python3
VENV ?= .venv
BIN := $(VENV)/bin

.PHONY: help venv install lint fmt typecheck test cov run openapi migrate migrate-new clean e2e-qemu-n0 e2e-qemu-n0-clean e2e-qemu-n0-logs

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

migrate: ## Apply pending Alembic migrations (uses SDN_DATABASE_URL or alembic.ini)
	$(BIN)/alembic upgrade head

migrate-new: ## Autogenerate a new Alembic revision; pass m="message"
	@if [ -z "$(m)" ]; then echo "usage: make migrate-new m=\"short description\""; exit 1; fi
	$(BIN)/alembic revision --autogenerate -m "$(m)"

clean: ## Remove caches and build artifacts
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache htmlcov coverage.xml .coverage
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

e2e-qemu-n0: ## Run N0 real-environment E2E tests in QEMU on rpi4-codex
	bash scripts/e2e/qemu-n0.sh

e2e-qemu-n0-clean: ## Stop QEMU/tunnels for the N0 E2E stand
	bash scripts/e2e/qemu-cleanup.sh

e2e-qemu-n0-logs: ## Collect QEMU/Nervum logs for the N0 E2E stand
	bash scripts/e2e/qemu-collect-logs.sh
