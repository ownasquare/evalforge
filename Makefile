.DEFAULT_GOAL := help

.PHONY: help sync lock test coverage lint format typecheck security check migrate seed doctor api ui demo compose-up compose-down

help:
	@echo "sync          Install the locked runtime and development environment"
	@echo "test          Run deterministic tests without provider calls"
	@echo "check         Run lint, formatting, typing, security, and coverage"
	@echo "migrate       Apply database migrations"
	@echo "seed          Seed the deterministic portfolio demo"
	@echo "doctor        Check local configuration and readiness"
	@echo "api           Start FastAPI on loopback port 8000"
	@echo "ui            Start Streamlit on loopback port 8501"
	@echo "demo          Prepare and run the complete offline demo"
	@echo "compose-up    Start the production-shaped local stack"

sync:
	uv sync --all-groups --frozen

lock:
	uv lock

test:
	uv run --all-groups pytest -q

coverage:
	uv run --all-groups pytest -q --cov=evalforge --cov-branch --cov-report=term-missing

lint:
	uv run --all-groups ruff check .
	uv run --all-groups ruff format --check .

format:
	uv run --all-groups ruff check --fix .
	uv run --all-groups ruff format .

typecheck:
	uv run --all-groups mypy src

security:
	uv run --all-groups bandit -q -c pyproject.toml -r src
	uv run --all-groups pip-audit

check: lint typecheck security coverage

migrate:
	uv run alembic upgrade head

seed:
	uv run evalforge seed

doctor:
	uv run evalforge doctor

api:
	uv run evalforge api

ui:
	uv run evalforge ui

demo:
	uv run evalforge demo

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down
