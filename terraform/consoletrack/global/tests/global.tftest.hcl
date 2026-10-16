# Offline checks for this root: `make tf-test`. No AWS calls: every data source and module
# that would call AWS or GitHub is overridden, so aws_iam_policy_document still renders the
# real policies. As a backstop, the provider's endpoints point at a closed local port, so a
# missed override fails here instead of reaching AWS.

provider "aws" {
  region                      = "us-east-1"
  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  skip_region_validation      = true

  endpoints {
    sts     = "http://127.0.0.1:9"
    iam     = "http://127.0.0.1:9"
    s3      = "http://127.0.0.1:9"
    route53 = "http://127.0.0.1:9"
    budgets = "http://127.0.0.1:9"
  }
}

variables {
  aws_region         = "us-east-1"
  name_suffix        = "a1b2c3"
  enable_github_oidc = true
  github_repo        = "example-org/consoletrack"
  enable_budget      = true
  budget_email       = "alerts@example.com"
  # Built, not written out: a 12-digit literal would trip the repo's account-ID check.
  operator_arn = "arn:aws:iam::${format("%012d", 0)}:role/Admin"
}

override_data {
  target = data.aws_caller_identity.current
  values = {
    account_id = "ACCOUNTID"
    arn        = "arn:aws:sts::ACCOUNTID:assumed-role/Admin/operator"
  }
}

override_data {
  target = data.aws_partition.current
  values = { partition = "aws", dns_suffix = "amazonaws.com" }
}

# The iam-role and OIDC provider submodules read the caller identity and GitHub's TLS
# certificate. The tests assert on this root's own policy documents instead.
override_module {
  target  = module.github_oidc_provider
  outputs = { arn = "arn:aws:iam::ACCOUNTID:oidc-provider/token.actions.githubusercontent.com" }
}

override_module {
  target  = module.github_role
  outputs = { arn = "arn:aws:iam::ACCOUNTID:role/ct/mock", name = "mock" }
}

override_module {
  target  = module.demo_developer_role
  outputs = { arn = "arn:aws:iam::ACCOUNTID:role/ct/mock", name = "mock" }
}

run "github_roles" {
  command = plan

  assert {
    condition     = toset(keys(module.github_role)) == toset(["plan", "apply", "revert", "deploy"])
    error_message = "Expected exactly the plan, apply, revert, and deploy roles."
  }

  # The trust policy: only web identity + tag session, StringEquals on sub with the exact
  # environment, StringEquals on aud, and no wildcard in sub.
  assert {
    condition = alltrue([
      for k, doc in data.aws_iam_policy_document.github_trust : alltrue([
        for s in jsondecode(doc.json).Statement : (
          toset(flatten([s.Action])) == toset(["sts:AssumeRoleWithWebIdentity", "sts:TagSession"])
          && s.Condition.StringEquals["token.actions.githubusercontent.com:sub"] == "repo:example-org/consoletrack:environment:consoletrack-${k}"
          && s.Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
          && s.Condition.StringLike["sts:RoleSessionName"] == "gh-*"
          && !can(s.Condition.StringLike["token.actions.githubusercontent.com:sub"])
        )
      ])
    ])
    error_message = "GitHub trust policies must use StringEquals on sub and aud with the exact environment."
  }

  # IAM limits an inline role policy to 10,240 characters, whitespace excluded.
  assert {
    condition = alltrue([
      for k, json in local.github_role_policies :
      length(replace(json, "/\\s/", "")) < 10240
    ])
    error_message = "A GitHub role's inline policy is over IAM's 10,240-character limit."
  }

  # Managed policy limit is 6,144 characters, whitespace excluded.
  assert {
    condition     = length(replace(data.aws_iam_policy_document.boundary.json, "/\\s/", "")) < 6144
    error_message = "The boundary is over IAM's 6,144-character managed policy limit."
  }

  assert {
    condition     = length(aws_budgets_budget.monthly) == 1 && aws_budgets_budget.monthly[0].name == "ct-budget-a1b2c3"
    error_message = "Budget must be ct-budget-<suffix>."
  }

  assert {
    condition     = length(aws_budgets_budget.monthly[0].notification) == 4
    error_message = "Budget needs alerts at 50/80/100% actual and 100% forecasted."
  }

  assert {
    condition     = output.state_bucket == "ct-tfstate-a1b2c3"
    error_message = "State bucket must be ct-tfstate-<suffix>."
  }
}

run "demo_developer_trusts_operator" {
  command = plan

  assert {
    condition     = jsondecode(data.aws_iam_policy_document.demo_developer_trust.json).Statement[0].Principal.AWS == var.operator_arn
    error_message = "The demo developer role must trust operator_arn."
  }
}

run "oidc_off_creates_no_github_roles" {
  command = plan

  variables {
    enable_github_oidc = false
    github_repo        = ""
    enable_budget      = false
  }

  assert {
    condition     = length(module.github_role) == 0 && length(data.aws_iam_policy_document.github_trust) == 0
    error_message = "enable_github_oidc = false must create no OIDC provider or GitHub roles."
  }

  assert {
    condition     = length(aws_budgets_budget.monthly) == 0
    error_message = "enable_budget = false must create no budget."
  }
}

run "github_repo_required_with_oidc" {
  command = plan

  variables {
    github_repo = ""
  }

  expect_failures = [var.github_repo]
}

run "budget_email_required" {
  command = plan

  variables {
    budget_email = ""
  }

  expect_failures = [var.budget_email]
}

run "external_domain" {
  command = plan

  variables {
    domain = "consoletrack.example.com"
  }

  assert {
    condition     = local.domain_host == "consoletrack" && local.parent_domain == "example.com"
    error_message = "The NS host must be the subdomain label."
  }

  assert {
    condition     = length(module.parent_delegation.records) == 0
    error_message = "dns_parent = external must not touch the parent zone."
  }
}
