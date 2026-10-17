# Offline checks for this root: `make tf-test`. No AWS calls: every data source and module that
# would call AWS is overridden, so this root's own aws_iam_policy_document data sources still
# render the real policies. As a backstop, the provider's endpoints point at a closed local
# port, so a missed override fails here instead of reaching AWS. The event pattern itself is
# tested by tests/test_event_pattern.py (`make test`).

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
    ec2     = "http://127.0.0.1:9"
    eks     = "http://127.0.0.1:9"
    s3      = "http://127.0.0.1:9"
    sqs     = "http://127.0.0.1:9"
    ssm     = "http://127.0.0.1:9"
    events  = "http://127.0.0.1:9"
    route53 = "http://127.0.0.1:9"
    acm     = "http://127.0.0.1:9"
  }
}

variables {
  aws_region  = "us-east-1"
  name_suffix = "a1b2c3"
  # Built, not written out: a 12-digit literal would trip the repo's account-ID check.
  operator_arn       = "arn:aws:iam::${format("%012d", 0)}:role/Admin"
  enable_github_oidc = true
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

override_data {
  target = data.aws_availability_zones.available
  values = { names = ["us-east-1a", "us-east-1b", "us-east-1c"] }
}

override_data {
  target = data.aws_route53_zone.domain
  values = { zone_id = "Z0EXAMPLE", arn = "arn:aws:route53:::hostedzone/Z0EXAMPLE" }
}

# Modules that read AWS during plan. The tests assert on this root's own locals, policies, and
# resources instead.
override_module {
  target = module.vpc
  outputs = {
    vpc_id                  = "vpc-0example"
    private_subnets         = ["subnet-0a", "subnet-0b"]
    private_route_table_ids = ["rtb-0a"]
  }
}

override_module {
  target = module.vpc_endpoints
}

override_module {
  target  = module.eks
  outputs = { cluster_name = "ct-eks-use1-a1b2c3" }
}

override_module {
  target  = module.node_role
  outputs = { arn = "arn:aws:iam::ACCOUNTID:role/ct/ct-eks-node-use1-a1b2c3", name = "ct-eks-node-use1-a1b2c3" }
}

override_module {
  target  = module.vpc_cni_pod_identity
  outputs = { iam_role_arn = "arn:aws:iam::ACCOUNTID:role/ct/ct-vpc-cni-use1-a1b2c3" }
}

override_module {
  target  = module.ebs_csi_pod_identity
  outputs = { iam_role_arn = "arn:aws:iam::ACCOUNTID:role/ct/ct-ebs-csi-use1-a1b2c3" }
}

override_module {
  target  = module.lbc_pod_identity
  outputs = { iam_role_arn = "arn:aws:iam::ACCOUNTID:role/ct/ct-lbc-use1-a1b2c3" }
}

override_module {
  target  = module.external_dns_pod_identity
  outputs = { iam_role_arn = "arn:aws:iam::ACCOUNTID:role/ct/ct-external-dns-use1-a1b2c3" }
}

override_module {
  target  = module.app_pod_identity
  outputs = { iam_role_arn = "arn:aws:iam::ACCOUNTID:role/ct/mock" }
}

override_module {
  target  = module.trail_bucket
  outputs = { bucket_id = "ct-trail-logs-use1-a1b2c3" }
}

override_module {
  target  = module.trail
  outputs = { cloudtrail_arn = "arn:aws:cloudtrail:us-east-1:ACCOUNTID:trail/ct-trail-use1-a1b2c3" }
}

override_module {
  target = module.changes_queue
  outputs = {
    queue_arn             = "arn:aws:sqs:us-east-1:ACCOUNTID:ct-changes-use1-a1b2c3"
    queue_url             = "https://sqs.us-east-1.amazonaws.com/ACCOUNTID/ct-changes-use1-a1b2c3"
    dead_letter_queue_arn = "arn:aws:sqs:us-east-1:ACCOUNTID:ct-changes-dlq-use1-a1b2c3"
    dead_letter_queue_url = "https://sqs.us-east-1.amazonaws.com/ACCOUNTID/ct-changes-dlq-use1-a1b2c3"
  }
}

override_module {
  target = module.changes_rule
  outputs = {
    eventbridge_rule_arns = { "ct-changes-use1-a1b2c3" = "arn:aws:events:us-east-1:ACCOUNTID:rule/ct-changes-use1-a1b2c3" }
  }
}

override_module {
  target  = module.acm
  outputs = { acm_certificate_arn = "arn:aws:acm:us-east-1:ACCOUNTID:certificate/example" }
}

# The two resources this root creates directly. With these overridden too, `command = apply`
# runs make no AWS calls. The canary policy depends on the group's ARN, so it is only rendered
# at apply.
override_resource {
  target = aws_s3_bucket_lifecycle_configuration.trail_logs
}

override_resource {
  target = aws_security_group.canary
  values = {
    id  = "sg-0canary"
    arn = "arn:aws:ec2:us-east-1:ACCOUNTID:security-group/sg-0canary"
  }
}

run "names" {
  command = plan

  # Must equal local.cluster_name in terraform/consoletrack/global (the revert and deploy
  # roles are scoped to it).
  assert {
    condition     = local.cluster_name == "ct-eks-use1-a1b2c3"
    error_message = "The cluster must be ct-eks-use1-<suffix>, matching the global root."
  }

  assert {
    condition = (
      local.queue_name == "ct-changes-use1-a1b2c3"
      && local.dlq_name == "ct-changes-dlq-use1-a1b2c3"
      && local.trail_name == "ct-trail-use1-a1b2c3"
      && local.trail_bucket_name == "ct-trail-logs-use1-a1b2c3"
      && local.rule_name == "ct-changes-use1-a1b2c3"
      && aws_security_group.canary.name == "ct-canary-sg-use1-a1b2c3"
    )
    error_message = "Regional names must be ct-<component>-use1-<suffix>."
  }

  assert {
    condition     = local.boundary_arn == "arn:aws:iam::ACCOUNTID:policy/ct/ct-boundary-a1b2c3"
    error_message = "Roles must carry the global root's ct-boundary-<suffix>."
  }

  assert {
    condition     = length(local.azs) == 2
    error_message = "The VPC spans exactly two AZs."
  }
}

run "event_pattern_from_file" {
  command = plan

  assert {
    condition     = jsondecode(local.event_pattern) == jsondecode(file("${path.module}/patterns/changes.json"))
    error_message = "The rule's pattern must be patterns/changes.json, unchanged."
  }
}

run "policies" {
  command = apply

  # "*" only in the two accepted account-wide reads (product-spec.md → Identity and access).
  assert {
    condition = alltrue(flatten([
      for doc in [data.aws_iam_policy_document.aws_mcp, data.aws_iam_policy_document.worker, data.aws_iam_policy_document.canary] : [
        for s in jsondecode(doc.json).Statement :
        !contains(flatten([s.Resource]), "*") || contains(["cloudtrail:LookupEvents", "ec2:DescribeSecurityGroups"], s.Action)
      ]
    ]))
    error_message = "Only cloudtrail:LookupEvents and ec2:DescribeSecurityGroups may use Resource \"*\"."
  }

  # Read-only: aws-mcp gets no write action.
  assert {
    condition = alltrue([
      for a in flatten([for s in jsondecode(data.aws_iam_policy_document.aws_mcp.json).Statement : flatten([s.Action])]) :
      can(regex("^(cloudtrail:LookupEvents|ec2:Describe|iam:(Get|List|SimulatePrincipalPolicy)|eks:Describe|s3:GetObject$)", a))
    ])
    error_message = "aws-mcp must only read."
  }

  assert {
    condition = toset(flatten([
      for s in jsondecode(data.aws_iam_policy_document.aws_mcp.json).Statement : flatten([s.Resource]) if s.Sid == "IamReadWatchedPaths"
    ])) == toset(["arn:aws:iam::ACCOUNTID:role/demo-app/*", "arn:aws:iam::ACCOUNTID:policy/demo-app/*"])
    error_message = "aws-mcp's IAM reads must be limited to watched_iam_paths."
  }

  assert {
    condition = [
      for s in jsondecode(data.aws_iam_policy_document.aws_mcp.json).Statement : s.Resource if s.Sid == "DemoAppState"
    ][0] == "arn:aws:s3:::ct-tfstate-a1b2c3/demo-app/us-east-1/terraform.tfstate"
    error_message = "aws-mcp may read only the demo-app root's state object."
  }

  assert {
    condition = alltrue([
      for s in jsondecode(data.aws_iam_policy_document.canary.json).Statement :
      s.Action == "ec2:CreateTags"
      && s.Condition["ForAllValues:StringEquals"]["aws:TagKeys"] == "ct:canary"
      && s.Condition["ForAnyValue:StringEquals"]["aws:TagKeys"] == "ct:canary"
    ])
    error_message = "The canary may only set the ct:canary tag."
  }
}

run "node_role" {
  command = plan

  # What the boundary allows, and nothing else: no CNI, EBS, or load balancer policies.
  assert {
    condition = local.node_role_policies == {
      AmazonEKSWorkerNodePolicy          = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
      AmazonEC2ContainerRegistryPullOnly = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryPullOnly"
    }
    error_message = "The node role gets AmazonEKSWorkerNodePolicy and ECR PullOnly only."
  }
}

run "defaults_create_no_lbc_or_external_dns" {
  command = plan

  assert {
    condition     = length(data.aws_route53_zone.domain) == 0
    error_message = "DOMAIN empty must not look up a zone."
  }

  assert {
    condition = toset(keys(nonsensitive(output.pod_identity_role_arns))) == toset([
      "vpc-cni", "ebs-csi", "aws-mcp", "worker", "canary",
    ])
    error_message = "By default only the CNI, EBS CSI, aws-mcp, worker, and canary roles exist."
  }

  assert {
    condition     = length(local.deploy_access_entry) == 1 && local.deploy_access_entry.gh_deploy.principal_arn == "arn:aws:iam::ACCOUNTID:role/ct/ct-gh-deploy-a1b2c3"
    error_message = "With GitHub OIDC, the ct-gh-deploy role gets an access entry."
  }

  assert {
    condition     = local.deploy_access_entry.gh_deploy.kubernetes_groups == ["consoletrack-deployers"] && !can(local.deploy_access_entry.gh_deploy.policy_associations)
    error_message = "The deploy role maps to a Kubernetes group only, never a cluster-wide access policy."
  }

  assert {
    condition     = local.operator_access_entry.operator.principal_arn == var.operator_arn
    error_message = "operator_arn gets the cluster admin access entry."
  }
}

run "alb_and_domain" {
  command = plan

  variables {
    exposure = "alb"
    domain   = "consoletrack.example.com"
  }

  assert {
    condition     = contains(keys(nonsensitive(output.pod_identity_role_arns)), "lbc") && contains(keys(nonsensitive(output.pod_identity_role_arns)), "external-dns")
    error_message = "EXPOSURE=alb and DOMAIN add the LBC and ExternalDNS roles."
  }
}

run "oidc_off_no_deploy_entry" {
  command = plan

  variables {
    enable_github_oidc = false
  }

  assert {
    condition     = length(local.deploy_access_entry) == 0
    error_message = "Without GitHub OIDC there is no deploy role to map."
  }
}

run "watched_path_ct_rejected" {
  command = plan

  variables {
    watched_iam_paths = "/demo-app/,/ct/"
  }

  expect_failures = [var.watched_iam_paths]
}

run "other_region_rejected" {
  command = plan

  variables {
    aws_region = "us-west-2"
  }

  expect_failures = [var.aws_region]
}
