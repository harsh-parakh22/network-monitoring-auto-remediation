.PHONY: help build up down ps logs test lint typecheck fmt ci-clean demo-kill-app1 demo-recover

help:
	@echo "make build    - build all images"
	@echo "make up       - start the full stack"
	@echo "make down     - stop and remove everything"
	@echo "make test     - run the test suite"
	@echo "make lint     - ruff + mypy"

build:
	docker compose build

up:
	docker compose up -d

down:
	docker compose down -v

ps:
	docker compose ps

logs:
	docker compose logs -f monitor

test:
	.venv/Scripts/python -m pytest tests -q

lint:
	.venv/Scripts/python -m ruff check src tests
	.venv/Scripts/python -m mypy src

demo-kill-app1:
	curl -s -X POST http://localhost:8081/crash || docker stop app-01

demo-recover:
	docker start app-01
