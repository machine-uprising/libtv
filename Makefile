# LibTV dev tasks. Runtime code stays dependency-free and Python 3.8
# compatible; Poetry manages the dev toolchain only (see CLAUDE.md).

ADDON_ID := plugin.video.libtv
DIST     := dist
ZIP      := $(DIST)/$(ADDON_ID).zip

.DEFAULT_GOAL := help
.PHONY: help install test lint checker zip check clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Set up the dev environment (.venv in-project)
	poetry install

test: ## Run unit tests
	poetry run pytest

lint: ## Lint with ruff
	poetry run ruff check .

checker: ## Validate add-on structure with kodi-addon-checker
	poetry run kodi-addon-checker --branch omega .

# Packages COMMITTED state only and self-checks the result; see
# scripts/build_zip.py for why plain `git archive --format=zip` cannot be
# used (Kodi rejects its zips).
zip: ## Build the installable add-on zip from committed HEAD
	python3 scripts/build_zip.py

check: lint test checker ## Run lint, tests, and the add-on checker

clean: ## Remove build artifacts
	rm -rf $(DIST)
