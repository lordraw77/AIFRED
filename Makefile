# AIFRED developer and deployment shortcuts. Run `make help`.
SHELL := /bin/bash
PY ?= .venv/bin/python
.DEFAULT_GOAL := help

.PHONY: help build test lint run logs lock docker/build docker/run docker/stop unseal backup release

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^[a-zA-Z0-9_\/-]+:.*## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

build: ## Byte-compile the package (syntax check)
	$(PY) -m compileall -q aifred

test: ## Run the test suite (set AIFRED_TEST_OPENBAO_* to include the real-OpenBao tests)
	$(PY) -m pytest -q

lint: ## Static checks with ruff
	$(PY) -m ruff check aifred tests

run: ## Run the service locally (first start needs AIFRED_ADMIN_USER and AIFRED_ADMIN_PASSWORD)
	AIFRED_DATA_DIR=$${AIFRED_DATA_DIR:-./data} $(PY) -m aifred serve

logs: ## Follow the logs of the Docker stack
	docker compose logs -f --tail 100

lock: ## Regenerate requirements.lock (hash-pinned dependencies for the image)
	uv pip compile pyproject.toml --generate-hashes --python-version 3.13 -o requirements.lock

docker/build: ## Build the AIFRED image
	docker compose build aifred

docker/run: ## First run or restart of the whole stack (scripts/setup.sh, safe to repeat)
	scripts/setup.sh

docker/stop: ## Stop the stack; volumes and data are kept
	docker compose down

unseal: ## Unseal OpenBao after a restart
	scripts/unseal.sh

backup: ## Back up the AIFRED and OpenBao volumes into ./backups
	scripts/backup.sh all

release: ## Print the release commands (runs none of them)
	scripts/release.sh
