.PHONY: up down build logs shell migrate migrations seed test lint check superuser ps

up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose up -d --build

restart:
	docker compose restart backend

logs:
	docker compose logs -f backend

shell:
	docker compose exec backend bash

migrate:
	docker compose exec backend python manage.py migrate

migrations:
	docker compose exec backend python manage.py makemigrations $(app)

seed:
ifdef flush
	docker compose exec backend python manage.py seed --flush
else
	docker compose exec backend python manage.py seed
endif

test:
	docker compose exec backend python -m pytest --cov --cov-report=term-missing -v

lint:
	docker compose exec backend python -m ruff check . && docker compose exec backend python -m ruff format --check .

check: lint test

superuser:
	docker compose exec backend python manage.py createsuperuser

ps:
	docker compose ps

# Local dev (no Docker)
local-install:
	uv sync --all-extras

local-migrate:
	uv run python manage.py migrate

local-run:
	uv run python manage.py runserver

local-test:
	uv run python -m pytest --cov --cov-report=term-missing -v

local-lint:
	uv run ruff check . && uv run ruff format --check .
