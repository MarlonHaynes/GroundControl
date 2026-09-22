# GroundControl — canonical task definitions.
#
# On Windows without GNU Make, use the shim: .\make.ps1 <target>
# It dispatches to the same underlying commands.

COMPOSE := docker compose
API     := $(COMPOSE) exec -T api

.DEFAULT_GOAL := help
.PHONY: help dev up down logs build seed eval eval-full test test-cov lint fmt types migrate revision psql shell clean nuke

help: ## Show available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

dev: up logs ## Bring the stack up and follow logs

up: ## Start postgres + api + web
	$(COMPOSE) up -d --build
	@echo "api  -> http://localhost:8000/docs"
	@echo "web  -> http://localhost:3000"

down: ## Stop the stack (keeps the database volume)
	$(COMPOSE) down

logs: ## Follow logs for all services
	$(COMPOSE) logs -f

build: ## Rebuild images
	$(COMPOSE) build

seed: ## Load fixtures into a coherent demo state
	$(API) python -m scripts.seed

eval: ## Run the eval harness on a 20-case subset (iteration loop)
	$(API) python -m evals.run --limit 20

eval-full: ## Run the eval harness on the full dataset (phase gate)
	$(API) python -m evals.run

test: ## Run the test suite (no API calls, deterministic, free)
	$(API) python -m pytest -q -m "not llm"

test-cov: ## Run tests with a coverage report
	$(API) python -m pytest -q -m "not llm" --cov=. --cov-report=term-missing

lint: ## Lint the API and the web app
	$(API) ruff check .
	cd apps/web && npm run lint

fmt: ## Format the API and the web app
	$(API) ruff format .
	$(API) ruff check --fix .
	cd apps/web && npm run fmt

types: ## Regenerate TS types from the live OpenAPI spec
	cd apps/web && npm run gen:types

migrate: ## Apply migrations
	$(API) alembic upgrade head

revision: ## Autogenerate a migration: make revision m="add widgets"
	$(API) alembic revision --autogenerate -m "$(m)"

psql: ## Open a psql shell
	$(COMPOSE) exec db psql -U groundcontrol -d groundcontrol

shell: ## Open a shell in the api container
	$(COMPOSE) exec api bash

clean: ## Remove build artifacts and caches
	$(COMPOSE) down --remove-orphans
	rm -rf apps/web/.next services/api/.pytest_cache services/api/.ruff_cache

nuke: ## Stop everything and DESTROY the database volume
	$(COMPOSE) down -v --remove-orphans
