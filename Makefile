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
# Terraform plan/init: the backend bucket name and region. Credentials come from AWS_PROFILE
# locally or from the environment in CI (GitHub OIDC), so AWS_PROFILE isn't required here.
ENV_TF := AWS_REGION NAME_SUFFIX
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
# An ALB without DOMAIN serves plain HTTP on its AWS DNS name: the browser drops the Secure
# session cookie (login silently fails), and the admin password would cross the internet in
# clear text. So EXPOSURE=alb requires DOMAIN, which brings the ACM certificate and HTTPS.
ifeq ($(EXPOSURE),alb)
ENV_UP += DOMAIN
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
# ALLOWED_CIDRS, EKS_PUBLIC_ACCESS_CIDRS, and WATCHED_IAM_PATHS stay comma-separated strings;
# the roots split them.
TF_INPUTS := AWS_REGION NAME_SUFFIX GITHUB_REPO GITHUB_OWNER_ID GITHUB_REPO_ID ENABLE_GITHUB_OIDC \
	OPERATOR_ARN K8S_VERSION USE_SPOT NODE_INSTANCE_TYPE VPC_CIDR EKS_PUBLIC_ACCESS_CIDRS EXPOSURE \
	ALLOWED_CIDRS DOMAIN DNS_PARENT ENABLE_BUDGET BUDGET_LIMIT_USD BUDGET_EMAIL WATCHED_IAM_PATHS
tf_var = TF_VAR_$(shell echo '$(1)' | tr '[:upper:]' '[:lower:]')
$(foreach v,$(TF_INPUTS),$(if $(strip $($v)),$(eval export $(call tf_var,$v) = $$($v))))

# --- Tools ------------------------------------------------------------------------------------
# Pinned so `make lint` gives the same answer everywhere. Bump deliberately.
RUFF ?= uvx ruff@0.16.10
PYTEST ?= uvx pytest@9.1.1
# PyYAML matches backend/uv.lock.
PYTEST_YAML ?= uvx --with pyyaml==6.0.3 pytest@9.1.1
OPENAPI_VALIDATOR ?= uvx openapi-spec-validator@0.9.0
TERRAFORM ?= terraform
TFLINT ?= tflint
HELMFILE ?= helmfile
SHELLCHECK ?= uvx --from shellcheck-py@0.11.0.1 shellcheck
# Docker Compose v2 (the `docker compose` plugin), for docker-compose.yaml.
COMPOSE ?= docker compose
# The backend's own locked environment (backend/uv.lock); --locked fails if the lock is stale.
BACKEND_RUN ?= cd backend && uv run --locked
# k8s-mcp's own locked environment (mcp-server/k8s/uv.lock).
K8S_MCP_RUN ?= cd mcp-server/k8s && uv run --locked
# The frontend's locked node_modules (frontend/package-lock.json), installed on demand below.
FRONTEND_RUN ?= cd frontend && npm run --silent
FRONTEND_DEPS := frontend/node_modules/.package-lock.json
# The demo workload's own locked environment (demo-app/uv.lock).
DEMO_APP_RUN ?= cd demo-app && uv run --locked
# The tag the Helmfile `local` environment deploys (helm/environments/local.yaml → imageTag).
DEMO_APP_IMAGE := ghcr.io/jennasrunbooks/consoletrack-demo-app:local
DOCKER ?= docker
KIND ?= kind
# make local's kind cluster. Fixed: compose/kind.yaml and compose/kind-values.yaml name its
# control-plane container. Not KIND_CLUSTER, which scripts/test-k8s.sh reads for its throwaway
# cluster (and deletes): exported from here, `make test-k8s` would take this one.
LOCAL_CLUSTER := consoletrack
# `make local` with the kind cluster (default), or false for the Compose stack alone.
LOCAL_KIND ?= true
# Compose with kind's additions: the backend on kind's network, and k8s-mcp (the `kind` profile).
COMPOSE_KIND = $(COMPOSE) -f docker-compose.yaml -f compose/kind.yaml --profile kind

# Every release, ours and third-party (product-spec.md → Cluster add-ons). Terraform is AWS-only.
HELMFILE_STATE := helm/helmfile.yaml.gotmpl
# Helm, Helmfile, and helm-diff keep release records in ConfigMaps rather than Secrets, so CI's
# deploy group needs no Secret access (docs/permissions.md). Not overridable from .env or the
# shell: every run must use the same storage, or Helm can't find the existing releases.
override HELM_DRIVER := configmap
export HELM_DRIVER

# One Terraform root per folder (product-spec.md → Terraform layout).
TF_ROOTS := terraform/consoletrack/global terraform/consoletrack/us-east-1 terraform/demo-app/us-east-1

# ROOT=<account>/<scope> for the tf-* targets, e.g. ROOT=consoletrack/global.
ROOT ?=
TF_DIR = terraform/$(ROOT)
# The backend's bucket and region come from .env; each root's backend.tf sets its own key.
TF_BACKEND_ARGS = -backend-config=bucket=ct-tfstate-$(NAME_SUFFIX) -backend-config=region=$(AWS_REGION)
check_root = @case " $(TF_ROOTS) " in *" terraform/$(ROOT) "*) ;; \
	*) echo "make $@: set ROOT to one of: $(patsubst terraform/%,%,$(TF_ROOTS))" >&2; exit 1 ;; esac
# Applies are for a human at a terminal: refuse in CI and without a TTY. Terraform then shows
# the plan and asks for "yes" itself; -auto-approve is never passed.
check_human = @if [ -n "$$CI" ] || [ ! -t 0 ]; then \
	echo "make $@: applies only from an interactive terminal, by a human (never in CI)" >&2; exit 1; fi

# Stub for a target a later issue fills in: says where it belongs and fails.
todo = @echo "make $@: not implemented yet (see $(1))" >&2; exit 1

.DEFAULT_GOAL := help
.PHONY: help lint test fmt env-check env-check-up env-check-aws env-check-local env-check-tf tf-env \
	tf-validate tf-test tflint tf-init tf-plan tf-apply tf-output gh-secrets \
	test-hooks test-terraform lint-hooks lint-backend lint-k8s-mcp test-k8s-mcp lint-openapi lint-terraform lint-terraform-providers \
	lint-shell lint-frontend lint-compose lint-demo-app helm-lint helm-test db-sql hash-password \
	test-all test-backend triage-smoke test-demo-app demo-app-image test-frontend test-e2e screenshots test-contract test-compose test-integration test-k8s \
	preflight bootstrap up down down-all verify-clean addons deploy open \
	domain domain-zone domain-cert domain-destroy \
	local local-down local-fault local-incidents demo demo-replay demo-drift demo-identity revert eval audit-rbac

##@ Development

help: ## List targets
	@awk 'BEGIN {FS = ":.*## "} \
		/^##@/ {printf "\n%s\n", substr($$0, 5); next} \
		/^[a-z][a-z0-9-]*:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE)

lint: lint-hooks lint-backend lint-k8s-mcp lint-demo-app lint-openapi lint-shell lint-terraform-providers helm-lint lint-frontend lint-compose ## Lint and format checks (run before every commit)

test: test-hooks test-backend test-k8s-mcp test-demo-app test-contract test-terraform test-compose test-frontend ## Unit and contract tests (run before every commit)

test-all: test test-integration ## Every automated test: unit, contract, frontend, and integration (needs Docker)

fmt: ## Format Python and Terraform in place
	$(RUFF) format agent-hooks backend mcp-server/k8s demo-app
	$(RUFF) check --fix-only --select I backend mcp-server/k8s demo-app
	@if command -v $(TERRAFORM) >/dev/null; then $(TERRAFORM) fmt -recursive terraform/; \
		else echo "terraform not installed; skipped terraform fmt" >&2; fi

lint-hooks: ## ruff check + format check for agent-hooks/
	$(RUFF) check agent-hooks
	$(RUFF) format --check agent-hooks

lint-backend: ## ruff check + format check for backend/ (config in backend/pyproject.toml)
	$(RUFF) check backend
	$(RUFF) format --check backend

lint-k8s-mcp: ## ruff check + format check for mcp-server/k8s/ (config in its pyproject.toml)
	$(RUFF) check mcp-server/k8s
	$(RUFF) format --check mcp-server/k8s

lint-demo-app: ## ruff check + format check for demo-app/ (config in demo-app/pyproject.toml)
	$(RUFF) check demo-app
	$(RUFF) format --check demo-app

lint-openapi: ## Validate openapi.yaml (OpenAPI 3.1)
	$(OPENAPI_VALIDATOR) openapi.yaml

lint-terraform: ## terraform fmt check (CI job terraform-fmt)
	$(TERRAFORM) fmt -check -recursive terraform/

lint-terraform-providers: ## Fail if any Terraform root uses the helm or kubernetes provider (Helmfile owns every chart)
	@hits=$$(grep -rnE 'provider[[:space:]]+"(helm|kubernetes)"|hashicorp/(helm|kubernetes)\b' terraform \
		--include='*.tf' --include='.terraform.lock.hcl' --exclude-dir=.terraform); \
	if [ -n "$$hits" ]; then \
		echo "$$hits" >&2; \
		echo "make $@: Terraform manages AWS only and never installs anything inside Kubernetes;" \
			"put the release in $(HELMFILE_STATE) instead (AGENTS.md → Conventions → Helm)" >&2; \
		exit 1; \
	fi; \
	echo "make $@: no helm or kubernetes provider in terraform/"

# Each configuration helm-lint renders: eks with placeholder Terraform outputs, the same with
# EXPOSURE=alb and DOMAIN, and local (kind). No cluster, no AWS; helm/tests/values/ explains.
HELM_LINT_CONFIGS := eks eks-alb local
helm_config_args = $(strip \
	$(if $(filter eks,$(1)),-e eks --state-values-file $(CURDIR)/helm/tests/values/eks.yaml) \
	$(if $(filter eks-alb,$(1)),-e eks --state-values-file $(CURDIR)/helm/tests/values/eks.yaml \
		--state-values-file $(CURDIR)/helm/tests/values/alb.yaml) \
	$(if $(filter local,$(1)),-e local))

helm-lint: ## helmfile lint + template (eks, eks with alb, local), then helm-test on the rendered output
	@if ! command -v $(HELMFILE) >/dev/null; then \
		echo "make $@: $(HELMFILE) is not installed (see README → Prerequisites);" \
			"\`make helm-test\` checks the local charts with helm alone" >&2; exit 1; fi
	@set -e; out=$$(mktemp -d); trap 'rm -rf "$$out"' EXIT; \
	$(foreach c,$(HELM_LINT_CONFIGS), \
		echo "--- $(c): helmfile $(call helm_config_args,$(c)) lint / template"; \
		$(HELMFILE) --file $(HELMFILE_STATE) $(call helm_config_args,$(c)) lint; \
		$(HELMFILE) --file $(HELMFILE_STATE) $(call helm_config_args,$(c)) template \
			--output-dir "$$out/$(c)" >/dev/null;) \
	HELM_RENDER_DIR="$$out" $(MAKE) --no-print-directory helm-test

# Without HELM_RENDER_DIR the tests render our own charts with plain `helm template`.
helm-test: ## Checks over our charts' rendered manifests: pod security, probes, RBAC, secrets by name
	$(PYTEST_YAML) helm/tests -q -p no:cacheprovider
	$(RUFF) check helm/tests
	$(RUFF) format --check helm/tests

lint-compose: ## ruff for compose/, and `docker compose config` wherever Docker is installed (CI)
	$(RUFF) check compose
	$(RUFF) format --check compose
	@if ! command -v docker >/dev/null; then \
		echo "make $@: docker isn't installed (the devcontainer has none); skipped docker compose config, CI runs it"; \
		exit 0; \
	fi; \
	$(COMPOSE) config --quiet && echo "make $@: docker-compose.yaml is valid" && \
	$(COMPOSE_KIND) config --quiet && echo "make $@: docker-compose.yaml with compose/kind.yaml is valid"

lint-shell: ## shellcheck every script under scripts/ (pinned, through uvx)
	$(SHELLCHECK) $$(find scripts -name '*.sh' -type f | sort)

tflint: ## tflint every root with .tflint.hcl (bundled terraform ruleset, no plugin download)
	@for r in $(TF_ROOTS); do \
		if ls $$r/*.tf >/dev/null 2>&1; then \
			echo "$$r:"; $(TFLINT) --chdir=$$r --config=$(CURDIR)/.tflint.hcl || exit 1; \
		fi; \
	done

tf-validate: ## terraform init -backend=false + validate for every root (no AWS calls, no state)
	@for r in $(TF_ROOTS); do \
		if ls $$r/*.tf >/dev/null 2>&1; then \
			echo "$$r:"; \
			$(TERRAFORM) -chdir=$$r init -backend=false -input=false >/dev/null || exit 1; \
			$(TERRAFORM) -chdir=$$r validate || exit 1; \
		else \
			echo "$$r: no .tf files yet, skipped"; \
		fi; \
	done

tf-test: ## terraform test for every root with tests/ (offline: AWS calls are overridden)
	@for r in $(TF_ROOTS); do \
		if [ -d $$r/tests ]; then \
			echo "$$r:"; \
			$(TERRAFORM) -chdir=$$r init -backend=false -input=false >/dev/null || exit 1; \
			$(TERRAFORM) -chdir=$$r test || exit 1; \
		fi; \
	done

test-hooks: ## Tests for agent-hooks/
	$(PYTEST) agent-hooks/tests -q

test-terraform: ## Python tests under terraform/*/*/tests/ (event pattern; no AWS, no Terraform)
	$(PYTEST) $$(find terraform -path '*/.terraform' -prune -o -name 'test_*.py' -print | sort) -q

test-backend: ## Backend unit tests, with app/services/ coverage >= 90%
	$(BACKEND_RUN) pytest tests/unit -q -m "not integration" \
		--cov=app/services --cov-report=term-missing --cov-fail-under=90

test-demo-app: ## Demo app and traffic generator unit tests (loopback fakes only, no AWS)
	$(DEMO_APP_RUN) pytest -q

test-contract: ## FastAPI schema matches openapi.yaml
	$(BACKEND_RUN) pytest tests/contract -q

test-compose: ## docker-compose.yaml invariants: loopback ports, no literal secrets, pinned images (no Docker)
	$(PYTEST_YAML) compose/tests -q

test-integration: ## Backend integration tests on Postgres: Testcontainers (Docker) or CT_TEST_DATABASE_URL
	$(BACKEND_RUN) pytest tests/integration -q -m integration

demo-app-image: ## Build the demo app image (consoletrack-demo-app:local) for this machine's architecture
	$(DOCKER) build -t $(DEMO_APP_IMAGE) demo-app/

db-sql: ## Print the full schema migration as SQL, offline (no database needed)
	$(BACKEND_RUN) alembic upgrade head --sql

hash-password: ## Prompt for a password and print its argon2id hash for .env ($ doubled for make)
	@$(BACKEND_RUN) python -m app.hash_password

# npm ci (scripts off) whenever the lockfile is newer than the installed tree.
$(FRONTEND_DEPS): frontend/package-lock.json
	cd frontend && npm ci --ignore-scripts --no-audit --no-fund

lint-frontend: $(FRONTEND_DEPS) ## ESLint, tsc, and src/api/schema.d.ts up to date with openapi.yaml
	$(FRONTEND_RUN) lint
	$(FRONTEND_RUN) typecheck
	$(FRONTEND_RUN) check:api

# SKIP_E2E=1 leaves out the browser tests; CI's `test` job sets it because its own `e2e` job
# runs them with a Playwright-installed Chromium.
test-frontend: lint-frontend ## Frontend lint + typecheck, Vitest (MSW, no network), then test-e2e
	$(FRONTEND_RUN) test
ifneq ($(SKIP_E2E),1)
	$(MAKE) --no-print-directory test-e2e
endif

# Browsers: CHROMIUM_PATH (set in the devcontainer), else `npx playwright install chromium`.
test-e2e: $(FRONTEND_DEPS) ## Playwright in Chromium against `npm run dev:mock` (localhost only)
	$(FRONTEND_RUN) test:e2e

screenshots: $(FRONTEND_DEPS) ## Regenerate docs/screenshots/ from `npm run dev:mock` with Playwright
	$(FRONTEND_RUN) screenshots

test-k8s-mcp: ## k8s-mcp unit tests with a fake Kubernetes client (no cluster, no network)
	$(K8S_MCP_RUN) pytest tests/unit -q -m "not kind" \
		--cov=k8s_mcp.tools --cov=k8s_mcp.redact --cov-report=term-missing --cov-fail-under=90

test-k8s: ## k8s-mcp and the remediator's `kubectl auth can-i` checks against a throwaway kind cluster with the real RBAC (needs Docker; runs in CI)
	scripts/test-k8s.sh

##@ Configuration

env-check: env-check-up ## Everything `make up` needs; same as env-check-up

env-check-up: ## Required for preflight, up: AWS, API key, enabled features; alb needs DOMAIN
	@if [ "$(EXPOSURE)" = alb ] && [ -z "$(strip $(DOMAIN))" ]; then \
		echo "make $@: EXPOSURE=alb requires DOMAIN: the ALB serves HTTPS only (see .env.example)" >&2; \
	fi
	$(call check_env,$(ENV_UP))

env-check-aws: ## Required for AWS-only targets (deploy, down, scenarios, revert)
	$(call check_env,$(ENV_AWS))

env-check-local: ## Required for local, demo-replay, eval: the API key only
	$(call check_env,$(ENV_LOCAL))

env-check-tf: ## Required for tf-init, tf-plan, tf-apply: AWS_REGION and NAME_SUFFIX
	$(call check_env,$(ENV_TF))

tf-env: ## Show which .env names map to which TF_VAR_* and whether each is set (no values)
	@$(foreach v,$(TF_INPUTS),printf "  %-26s <- %-20s %s\n" "$(call tf_var,$v)" "$v" \
		"$(if $(strip $($v)),set,unset (Terraform default))";)

##@ AWS deployment

preflight: env-check-up ## Check tools, AWS identity, quotas, K8s version, API key, CAA (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

bootstrap: env-check-aws ## Create the Terraform state bucket (and print a NAME_SUFFIX if unset)
	scripts/bootstrap-state.sh

tf-init: env-check-tf ## terraform init ROOT=<account>/<scope> with the S3 backend from .env
	$(check_root)
	$(TERRAFORM) -chdir=$(TF_DIR) init -input=false $(TF_BACKEND_ARGS)

tf-plan: env-check-tf ## terraform plan ROOT=… with TF_VAR_* from .env (writes $(TF_DIR)/tfplan)
	$(check_root)
	$(TERRAFORM) -chdir=$(TF_DIR) plan -input=false -out=tfplan

tf-apply: env-check-tf ## terraform apply ROOT=… after Terraform's own yes prompt (humans only)
	$(check_root)
	$(check_human)
	$(TERRAFORM) -chdir=$(TF_DIR) apply

tf-output: env-check-tf ## terraform output ROOT=<account>/<scope> [OUTPUT=<name>] (read-only; sensitive values stay hidden)
	$(check_root)
	$(TERRAFORM) -chdir=$(TF_DIR) output $(OUTPUT)

up: env-check-up ## Create the platform, add-ons, and app; print the dashboard URL (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

addons: env-check-aws ## helmfile -l tier=addons apply: Gateway API CRDs and third-party charts (stub)
	$(call todo,#12)

deploy: env-check-aws ## helmfile -l tier=app apply: the consoletrack and demo-app releases (stub)
	$(call todo,#12)

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

gh-secrets: ## Set each GitHub environment's AWS_ROLE_ARN from the global root's outputs (humans only)
	$(call check_env,GITHUB_REPO)
	scripts/gh-env-secrets.sh

domain-zone: env-check-aws env-check-tf ## Apply only the Route 53 zone and print the NS records (humans only)
	@if [ -z "$(strip $(DOMAIN))" ]; then echo "make $@: DOMAIN is empty; set it in .env" >&2; exit 1; fi
	$(check_human)
	$(MAKE) --no-print-directory tf-init ROOT=consoletrack/global
	$(TERRAFORM) -chdir=terraform/consoletrack/global apply \
		-target=module.zone -target=module.parent_delegation
	@$(TERRAFORM) -chdir=terraform/consoletrack/global output -raw domain_ns_instructions; echo

domain-cert: env-check-aws ## Wait for delegation, then issue the ACM certificate (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

domain-destroy: env-check-aws ## Remove the zone (stub)
	$(call todo,the EKS + deploy pipeline workstream; no issue yet)

##@ Demo and evaluation

# The kind half (scripts/local-kind.sh) goes around Compose: the cluster, add-ons, demo app, the
# kubeconfigs (k8s-mcp's and the backend's remediator), and the remediation policies before it,
# the webhook token and AlertmanagerConfig after it (they need the running backend). The worker and aws-mcp join with #9 and #10 PR 2; see #15.
local: env-check-local ## Compose stack plus the `consoletrack` kind cluster with Prometheus and the demo app, no AWS (LOCAL_KIND=false: Compose only)
ifeq ($(LOCAL_KIND),true)
	$(MAKE) --no-print-directory demo-app-image
	scripts/local-kind.sh cluster
	$(COMPOSE_KIND) up --build --wait
	scripts/local-kind.sh alerts
else
	$(COMPOSE) up --build --wait
endif
	@echo "make $@: dashboard http://localhost:$(or $(DASHBOARD_PORT),8080), API http://localhost:$(or $(BACKEND_PORT),8000)"
	@if pw=$$($(COMPOSE) exec -T backend cat /run/consoletrack/admin-password 2>/dev/null); then \
		echo "make $@: log in as admin with the generated local password: $$pw"; \
	else \
		echo "make $@: log in as admin with the password for ADMIN_PASSWORD_HASH in $(ENV_FILE)"; \
	fi
ifeq ($(LOCAL_KIND),true)
	@echo "make $@: kind context kind-$(LOCAL_CLUSTER); Grafana: kubectl --context kind-$(LOCAL_CLUSTER)" \
		"-n monitoring port-forward svc/kube-prometheus-stack-grafana 3000:80"
endif

local-fault: ## Turn a demo-app fault on in kind (FAULT=oom|badImage|badProbe), or off (FAULT=off)
	scripts/local-kind.sh fault $(FAULT)

local-incidents: ## Local incidents with the agent's root cause and proposal (until the dashboard's Incidents page)
	scripts/local-kind.sh incidents

local-down: ## Remove the Compose stack and its volumes, and the `consoletrack` kind cluster if present
	@if command -v $(KIND) >/dev/null && $(KIND) get clusters 2>/dev/null | grep -qx $(LOCAL_CLUSTER); then \
		$(COMPOSE_KIND) down --volumes --remove-orphans && \
		$(KIND) delete cluster --name $(LOCAL_CLUSTER); \
	else \
		$(COMPOSE) down --volumes --remove-orphans; \
	fi
	rm -f kind-internal.kubeconfig kind-remediator.kubeconfig kind-remediation-policies.yaml

demo-replay: env-check-local ## Local stack with recorded CloudTrail events, no AWS (stub)
	$(call todo,#9 (scripts/replay-fixtures.py and the worker's replay mode); see #15)

demo: env-check-aws ## Run the three cluster fault scenarios (stub)
	$(call todo,the agent loop workstream; no issue yet)

demo-drift: env-check-aws ## Open port 22 on the demo security group as the developer role (stub)
	$(call todo,the cloud scenarios + revert workstream; no issue yet)

demo-identity: env-check-aws ## Remove s3:GetObject from the demo app role as the developer role (stub)
	$(call todo,the cloud scenarios + revert workstream; no issue yet)

revert: env-check-aws ## Plan and apply the demo-app root for CHANGE=<id>, with confirmation (stub)
	$(call todo,the cloud scenarios + revert workstream; no issue yet)

triage-smoke: env-check-local ## One real Messages API request with the triage prompt, tools, and TRIAGE_MODEL (needs ANTHROPIC_API_KEY)
	@tools=$$(mktemp) && trap 'rm -f "$$tools"' EXIT && \
	( $(K8S_MCP_RUN) env K8S_ALLOWED_NAMESPACES=demo python -m k8s_mcp.tool_definitions ) > "$$tools" && \
	( $(BACKEND_RUN) python -m app.agents.smoke --k8s-tools "$$tools" )

eval: env-check-local ## Agent eval against kind with the real model (stub)
	$(call todo,the tests + evals workstream; no issue yet)

audit-rbac: env-check-aws ## kubectl auth can-i --list for each ServiceAccount (stub)
	$(call todo,the tests + evals workstream; no issue yet)
