.DEFAULT_GOAL := help

.PHONY: help sync lock test coverage lint format typecheck security check migrate seed doctor api ui compose-up compose-down

help:
	@echo "sync          Install the locked runtime and development environment"
	@echo "test          Run deterministic tests without provider calls"
	@echo "check         Run lint, formatting, typing, security, and coverage"
	@echo "migrate       Apply database migrations"
	@echo "seed          Seed the deterministic portfolio demo"
	@echo "doctor        Check local configuration and readiness"
	@echo "api           Start FastAPI on loopback port 8000"
	@echo "ui            Start Streamlit on loopback port 8501"
	@echo "compose-up    Start the production-shaped local stack"

sync:
	uv sync --all-groups --frozen

lock:
	uv lock

test:
	uv run pytest -q

coverage:
	uv run pytest -q --cov=evalforge --cov-branch --cov-report=term-missing

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy src

security:
	uv run bandit -q -c pyproject.toml -r src
	uv run pip-audit

check: lint typecheck security coverage

migrate:
	uv run alembic upgrade head

seed:
	uv run evalforge seed

doctor:
	uv run evalforge doctor

api:
	uv run uvicorn evalforge.api.app:app --host 127.0.0.1 --port 8000 --workers 1

ui:
	uv run streamlit run src/evalforge/streamlit_app.py --server.address 127.0.0.1 --server.port 8501

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down
