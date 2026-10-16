.DEFAULT_GOAL := help

PYTHON ?= python3
DESKTOP_VENV ?= .venv-unix
DESKTOP_PY := $(DESKTOP_VENV)/bin/python

.PHONY: help desktop-setup desktop-run desktop-test web-setup web-start web-test web-build test version-check badge-check preflight docker-up docker-dev docker-down docker-logs

help: ## Show the available local commands.
	@awk 'BEGIN {FS = ":.*##"}; /^[a-zA-Z0-9_-]+:.*##/ {printf "%-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

desktop-setup: ## Install dependencies for the desktop edition.
	@if [ ! -x "$(DESKTOP_PY)" ]; then \
		echo "Creating desktop virtual environment in $(DESKTOP_VENV)..."; \
		$(PYTHON) -m venv "$(DESKTOP_VENV)"; \
	fi
	$(DESKTOP_PY) -m pip install --upgrade pip
	$(DESKTOP_PY) -m pip install -r requirements.txt

desktop-run: desktop-setup ## Install dependencies and start the desktop application (Linux, macOS, or WSL).
	$(DESKTOP_PY) ps2_save_tool.py

desktop-test: desktop-setup ## Install dependencies and run desktop Python tests.
	$(DESKTOP_PY) -m unittest discover -s tests -v

web-setup: ## Install web backend and frontend dependencies.
	bash web/setup.sh

web-start: ## Start the web development servers in the background.
	bash web/start-local.sh

web-test: web-setup ## Install dependencies and run web-backend Python tests.
	bash web/test.sh

web-build: web-setup ## Install dependencies and create a production frontend build.
	cd web/frontend && npm run build

test: desktop-test web-test ## Run every Python test suite.

version-check: ## Verify independent desktop and web semantic versions.
	$(PYTHON) scripts/check_versions.py

badge-check: ## Verify the README's live badge and GHCR package links.
	$(PYTHON) scripts/check_readme_badges.py

preflight: ## Log and run release checks, all tests, and the frontend production build.
	bash scripts/preflight.sh

docker-up: ## Pull and start the published self-hosted containers.
	cd web && docker compose pull && docker compose up -d

docker-dev: ## Build and start containers from this checkout.
	cd web && docker compose -f compose.yaml -f compose.dev.yaml up -d --build

docker-down: ## Stop the self-hosted Docker stack.
	cd web && docker compose down

docker-logs: ## Follow logs from the self-hosted Docker stack.
	cd web && docker compose logs -f
