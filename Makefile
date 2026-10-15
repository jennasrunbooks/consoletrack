# ConsoleTrack Makefile. Minimal for now: the full skeleton (up, deploy, demo-*, ...)
# comes in the next issue.

# Pinned so `make lint` gives the same answer everywhere. Bump deliberately.
RUFF ?= uvx ruff@0.16.10
PYTEST ?= uvx pytest@9.1.1

.DEFAULT_GOAL := help
.PHONY: help test lint test-hooks lint-hooks

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

test: test-hooks ## Unit tests (run before every commit)

lint: lint-hooks ## Lint and format checks

test-hooks: ## Tests for agent-hooks/
	$(PYTEST) agent-hooks/tests -q

lint-hooks: ## ruff check + format check for agent-hooks/
	$(RUFF) check agent-hooks
	$(RUFF) format --check agent-hooks
