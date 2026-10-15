# ConsoleTrack Makefile: the one interface for humans, the agent, and CI.
# `make help` lists every target. Targets marked "stub" exist so the interface is fixed early;
# they fail with the issue or workstream that fills them in.

# This file's own path, taken before .env is included (help must never read .env).
MAKEFILE := $(lastword $(MAKEFILE_LIST))

# --- Configuration from .env ------------------------------------------------------------------
# .env holds plain KEY=value lines (see .env.example). Anything already set in the shell wins,
# so `op run --env-file=.env -- make up` and keychain exports work: remember the shell's
# variables, include .env, then put back any shell variable that .env overrode. A secret can
# therefore be empty (or an op:// reference) in .env and still arrive from the shell.
# ENV_FILE points somewhere else for a second config or for testing.
# Recipes must never echo these values; print names only.
ENV_FILE ?= .env
_SHELL_VARS := $(foreach v,$(.VARIABLES),$(if $(filter environment,$(origin $v)),$v))
$(foreach v,$(_SHELL_VARS),$(eval _shell_$v := $$(value $v)))
-include $(ENV_FILE)
$(foreach v,$(_SHELL_VARS),$(if $(filter file,$(origin $v)),$(eval $v := $$(_shell_$v))))
export

# What each kind of target requires. lint, test, fmt, and help require none of these, so CI
# and reviewers without AWS access or an API key can always run them.
# AWS access only: teardown and scenarios, so `make down` works even without an API key.
ENV_AWS := AWS_PROFILE AWS_REGION
# Running the triage agent locally (kind, Compose, demo-replay, evals).
ENV_LOCAL := ANTHROPIC_API_KEY
# Standing up the platform: AWS, the API key it stores in the cluster, and settings that the
# enabled features need.
ENV_UP := $(ENV_AWS) ANTHROPIC_API_KEY
ifeq ($(ENABLE_GITHUB_OIDC),true)
ENV_UP += GITHUB_REPO
endif
ifeq ($(ENABLE_BUDGET),true)
ENV_UP += BUDGET_EMAIL
endif
ifneq ($(strip $(DOMAIN)),)
ENV_UP += DNS_PARENT
endif

# Fails listing the names in $(1) that are unset, empty, or still an unresolved op:// reference
# (`make` run without `op run`). Never prints a value.
check_env = @missing="$(strip $(foreach v,$(1),$(if $(filter-out op://%,$(strip $($v))),,$v)))"; \
	if [ -n "$$missing" ]; then \
		echo "make $@: missing required variables (set them in your shell or $(ENV_FILE);" \
			"an op:// reference needs \`op run --env-file=$(ENV_FILE) -- make $(MAKECMDGOALS)\`):" >&2; \
		for v in $$missing; do echo "  $$v" >&2; done; \
		exit 1; \
	fi; \
	echo "make $@: required variables are set: $(strip $(1))"

# .env names passed to Terraform as TF_VAR_<lowercase name>. Secrets such as ANTHROPIC_API_KEY
# are deliberately absent: they go to Kubernetes Secrets, never into Terraform state. Only
# non-empty values are exported, so Terraform falls back to the variable's default otherwise.
# ALLOWED_CIDRS and WATCHED_IAM_PATHS stay comma-separated strings; the roots split them.
TF_INPUTS := AWS_REGION NAME_SUFFIX GITHUB_REPO ENABLE_GITHUB_OIDC K8S_VERSION USE_SPOT \
	NODE_INSTANCE_TYPE EXPOSURE ALLOWED_CIDRS DOMAIN DNS_PARENT ENABLE_BUDGET BUDGET_LIMIT_USD \
	BUDGET_EMAIL WATCHED_IAM_PATHS
tf_var = TF_VAR_$(shell echo '$(1)' | tr '[:upper:]' '[:lower:]')
$(foreach v,$(TF_INPUTS),$(if $(strip $($v)),$(eval export $(call tf_var,$v) = $$($v))))

# --- Tools ------------------------------------------------------------------------------------
# Pinned so `make lint` gives the same answer everywhere. Bump deliberately.
RUFF ?= uvx ruff@0.16.10
PYTEST ?= uvx pytest@9.1.1
OPENAPI_VALIDATOR ?= uvx openapi-spec-validator@0.9.0
TERRAFORM ?= terraform

# Stub for a target a later issue fills in: says where it belongs and fails.
todo = @echo "make $@: not implemented yet (see $(1))" >&2; exit 1

.DEFAULT_GOAL := help
.PHONY: help lint test fmt env-check env-check-up env-check-aws env-check-local tf-env \
	test-hooks lint-hooks lint-openapi lint-terraform \
	test-backend test-frontend test-contract test-integration test-k8s \
	preflight bootstrap up down down-all verify-clean deploy open \
	domain domain-zone domain-cert domain-destroy \
	local demo demo-replay demo-drift demo-identity revert eval audit-rbac

##@ Development

help: ## List targets
	@awk 'BEGIN {FS = ":.*## "} \
		/^##@/ {printf "\n%s\n", substr($$0, 5); next} \
		/^[a-z][a-z0-9-]*:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE)

lint: lint-hooks lint-openapi ## Lint and format checks (run before every commit)

test: test-hooks ## Unit tests (run before every commit)

fmt: ## Format Python and Terraform in place
	$(RUFF) format agent-hooks
	@if command -v $(TERRAFORM) >/dev/null; then $(TERRAFORM) fmt -recursive terraform/; \
		else echo "terraform not installed; skipped terraform fmt" >&2; fi

lint-hooks: ## ruff check + format check for agent-hooks/
	$(RUFF) check agent-hooks
	$(RUFF) format --check agent-hooks

lint-openapi: ## Validate openapi.yaml (OpenAPI 3.1)
	$(OPENAPI_VALIDATOR) openapi.yaml

lint-terraform: ## terraform fmt check (CI job terraform-fmt)
	$(TERRAFORM) fmt -check -recursive terraform/

test-hooks: ## Tests for agent-hooks/
	$(PYTEST) agent-hooks/tests -q

test-backend: ## Backend unit tests (stub)
	$(call todo,#4)

test-contract: ## FastAPI schema matches openapi.yaml (stub)
	$(call todo,#4)

test-integration: ## Backend integration tests, needs Docker (stub)
	$(call todo,#4)

test-frontend: ## Frontend lint and Vitest (stub)
	$(call todo,the frontend + change log workstream; no issue yet)

test-k8s: ## k8s-mcp against kind with real RBAC (stub)
	$(call todo,#10)

##@ Configuration

env-check: env-check-up ## Everything `make up` needs; same as env-check-up

env-check-up: ## Required for preflight, up: AWS, API key, enabled features (names only)
	$(call check_env,$(ENV_UP))

env-check-aws: ## Required for AWS-only targets (deploy, down, scenarios, revert)
	$(call check_env,$(ENV_AWS))

env-check-local: ## Required for local, demo-replay, eval: the API key only
	$(call check_env,$(ENV_LOCAL))

tf-env: ## Show which .env names map to which TF_VAR_* and whether each is set (no values)
	@$(foreach v,$(TF_INPUTS),printf "  %-26s <- %-20s %s\n" "$(call tf_var,$v)" "$v" \
		"$(if $(strip $($v)),set,unset (Terraform default))";)

##@ AWS deployment

preflight: env-check-up ## Check tools, AWS identity, quotas, K8s version, API key, CAA (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

bootstrap: env-check-aws ## Create the Terraform state bucket and NAME_SUFFIX (stub)
	$(call todo,#3)

up: env-check-up ## Create the platform, add-ons, and app; print the dashboard URL (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

deploy: env-check-aws ## helm upgrade the consoletrack chart (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

open: env-check-aws ## Port-forward the dashboard and open it (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

down: env-check-aws ## Destroy demo-app and the regional platform; keep global and state (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

down-all: env-check-aws ## down, plus the global root and the state bucket (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

verify-clean: env-check-aws ## Fail if project-tagged billable resources remain (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

##@ Custom domain

domain: env-check-aws ## Zone and certificate in one run (DNS_PARENT=route53) (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

domain-zone: env-check-aws ## Create the Route 53 zone and print the NS records (stub)
	$(call todo,#3)

domain-cert: env-check-aws ## Wait for delegation, then issue the ACM certificate (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

domain-destroy: env-check-aws ## Remove the zone (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

##@ Demo and evaluation

local: env-check-local ## Full stack on kind + Compose, no AWS (stub)
	$(call todo,the local-run workstream; no issue yet)

demo-replay: env-check-local ## Local stack with recorded CloudTrail events, no AWS (stub)
	$(call todo,the local-run workstream; no issue yet. #9 provides scripts/replay-fixtures.py)

demo: env-check-aws ## Run the three cluster fault scenarios (stub)
	$(call todo,the agent loop workstream; no issue yet)

demo-drift: env-check-aws ## Open port 22 on the demo security group as the developer role (stub)
	$(call todo,the cloud scenarios + revert workstream; no issue yet)

demo-identity: env-check-aws ## Remove s3:GetObject from the demo app role as the developer role (stub)
	$(call todo,the cloud scenarios + revert workstream; no issue yet)

revert: env-check-aws ## Plan and apply the demo-app root for CHANGE=<id>, with confirmation (stub)
	$(call todo,the cloud scenarios + revert workstream; no issue yet)

eval: env-check-local ## Agent eval against kind with the real model (stub)
	$(call todo,the tests + evals workstream; no issue yet)

audit-rbac: env-check-aws ## kubectl auth can-i --list for each ServiceAccount (stub)
	$(call todo,the tests + evals workstream; no issue yet)
