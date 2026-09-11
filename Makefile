.PHONY: up down logs new-project test lint typecheck

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

new-project:
	@echo "New project_id:"
	@python3 -c "import uuid; print(uuid.uuid4())"
	@echo
	@echo "Add mount to docker-compose.yml:"
	@echo "  \$$HOME/Vaults/<project-folder>:/vault/<project-folder>"
	@echo "Add config entry to config/projects.json:"
	@echo "  {\"id\":\"<uuid4>\",\"name\":\"<project-name>\",\"folder\":\"<project-folder>\"}"

test:
	poetry run pytest

lint:
	poetry run ruff check .
	poetry run ruff format --check .

typecheck:
	poetry run mypy src
