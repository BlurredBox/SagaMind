.DEFAULT_GOAL := help
.PHONY: help install dev lint format type test cover lock audit research external-validation integration migrate run demo grpc proto docker clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install runtime dependencies
	pip install -e .

dev: ## Install dev + dashboard + wasm extras
	pip install -e ".[dev,dashboard,wasm]"

lint: ## Lint with ruff
	ruff check .

format: ## Auto-format with ruff
	ruff format .
	ruff check . --fix

type: ## Static type-check
	mypy src

test: ## Run the test suite
	pytest -q

cover: ## Run tests with coverage gate
	pytest --cov=src --cov-report=term-missing --cov-report=json:coverage.json --cov-fail-under=70
	python scripts/check_critical_coverage.py coverage.json

lock: ## Rebuild the Python 3.11 runtime dependency lock
	pip-compile pyproject.toml --output-file=requirements.lock --strip-extras --resolver=backtracking

audit: ## Fail on known vulnerabilities in locked runtime dependencies
	pip-audit -r requirements.lock

research: ## Reproduce controlled scientific evaluation artifacts
	python -m experiments.evaluate

external-validation: ## Run named public baselines and verify replication artifacts
	python -m external_validation.compare
	python -m external_validation.verify_results
	python -m external_validation.render_report

integration: ## Run integration tests against live backends (needs docker compose up)
	RUN_INTEGRATION=1 pytest -m integration

migrate: ## Apply database migrations (needs .[migrations] and a reachable DB)
	alembic upgrade head

run: ## Start the REST API
	python -m uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

grpc: ## Start the gRPC server (requires `make proto`)
	python -m src.grpc_server

proto: ## Generate gRPC stubs from proto/
	./scripts/gen_proto.sh

demo: ## Launch the Streamlit dashboard
	streamlit run app_demo.py

docker: ## Build and run the full stack
	docker compose up --build

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage build dist *.egg-info
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
