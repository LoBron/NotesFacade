.PHONY: bootstrap check-runtime up down logs new-project test lint typecheck

bootstrap:
	./scripts/bootstrap.sh

check-runtime:
	./scripts/bootstrap.sh --check-runtime

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f

new-project:
	@echo "New project_id:"
	@if [ -r /proc/sys/kernel/random/uuid ]; then \
		cat /proc/sys/kernel/random/uuid; \
	elif command -v uuidgen >/dev/null 2>&1; then \
		uuidgen; \
	else \
		echo "Cannot generate UUID: /proc UUID source and uuidgen are unavailable." >&2; \
		exit 1; \
	fi
	@echo
	@echo "For additional projects, add matching read-write/read-only mounts as documented in README.md."
	@echo "Add config entry to config/projects.json:"
	@echo "  {\"id\":\"<uuid4>\",\"name\":\"<project-name>\",\"folder\":\"<project-folder>\"}"

test:
	poetry run pytest

lint:
	poetry run ruff check .
	poetry run ruff format --check .

typecheck:
	poetry run mypy src
