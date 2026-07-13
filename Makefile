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

# git archive packages COMMITTED state only, so refuse to build with a dirty
# tree — otherwise the zip silently omits your latest changes. Untracked
# files count as dirty: an overly-broad .gitignore once swallowed
# resources/lib entirely, so the built zip is also checked for key contents.
zip: ## Build the installable add-on zip from committed HEAD
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "error: uncommitted or untracked changes — 'git archive' packages HEAD only; commit first."; \
		exit 1; \
	fi
	@mkdir -p $(DIST)
	git archive --format=zip --prefix=$(ADDON_ID)/ -o $(ZIP) HEAD
	@for f in addon.xml default.py service.py resources/lib/libtv/plugin.py resources/settings.xml; do \
		if ! unzip -l $(ZIP) | grep -q "$(ADDON_ID)/$$f"; then \
			echo "error: built zip is missing $$f — check .gitignore / .gitattributes"; \
			rm -f $(ZIP); \
			exit 1; \
		fi; \
	done
	@echo "built $(ZIP)"

check: lint test checker ## Run lint, tests, and the add-on checker

clean: ## Remove build artifacts
	rm -rf $(DIST)
