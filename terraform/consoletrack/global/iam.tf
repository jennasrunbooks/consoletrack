################################################################################
# Shared names and ARNs
################################################################################

locals {
  iam_arn = "arn:${local.partition}:iam::${local.account_id}"

  # Built from its name, not the module output, so the boundary can refer to itself.
  boundary_name = "${local.name_prefix}-boundary-${var.name_suffix}"
  boundary_arn  = "${local.iam_arn}:policy/ct/${local.boundary_name}"

  github_oidc_host         = "token.actions.githubusercontent.com"
  github_oidc_provider_arn = "${local.iam_arn}:oidc-provider/${local.github_oidc_host}"

  # The only IAM paths project roles may create or change (product-spec.md → Name).
  project_iam_arns = [
    "${local.iam_arn}:role/ct/*",
    "${local.iam_arn}:role/demo-app/*",
    "${local.iam_arn}:policy/ct/*",
    "${local.iam_arn}:policy/demo-app/*",
    "${local.iam_arn}:instance-profile/ct/*",
    "${local.iam_arn}:instance-profile/demo-app/*",
  ]
  project_role_arns = [
    "${local.iam_arn}:role/ct/*",
    "${local.iam_arn}:role/demo-app/*",
  ]

  # What terraform/demo-app/us-east-1 manages in IAM: demo-app-* under /demo-app/.
  demo_app_iam_arns = [
    "${local.iam_arn}:role/demo-app/demo-app-*",
    "${local.iam_arn}:policy/demo-app/demo-app-*",
  ]

  # Every action that creates or changes an IAM role, managed policy, or instance profile.
  # DeleteRolePermissionsBoundary is left out on purpose: the boundary denies it outright.
  iam_write_actions = [
    "iam:CreateRole",
    "iam:DeleteRole",
    "iam:UpdateRole",
    "iam:UpdateRoleDescription",
    "iam:UpdateAssumeRolePolicy",
    "iam:TagRole",
    "iam:UntagRole",
    "iam:PutRolePolicy",
    "iam:DeleteRolePolicy",
    "iam:AttachRolePolicy",
    "iam:DetachRolePolicy",
    "iam:PutRolePermissionsBoundary",
    "iam:CreatePolicy",
    "iam:DeletePolicy",
    "iam:CreatePolicyVersion",
    "iam:DeletePolicyVersion",
    "iam:SetDefaultPolicyVersion",
    "iam:TagPolicy",
    "iam:UntagPolicy",
    "iam:CreateInstanceProfile",
    "iam:DeleteInstanceProfile",
    "iam:AddRoleToInstanceProfile",
    "iam:RemoveRoleFromInstanceProfile",
    "iam:TagInstanceProfile",
    "iam:UntagInstanceProfile",
  ]

  # Read-side IAM calls Terraform makes when refreshing roles, policies, and instance profiles.
  iam_read_actions = [
    "iam:GetRole",
    "iam:GetRolePolicy",
    "iam:ListRolePolicies",
    "iam:ListAttachedRolePolicies",
    "iam:ListInstanceProfilesForRole",
    "iam:ListRoleTags",
    "iam:GetPolicy",
    "iam:GetPolicyVersion",
    "iam:ListPolicyVersions",
    "iam:ListPolicyTags",
    "iam:ListEntitiesForPolicy",
    "iam:GetInstanceProfile",
    "iam:ListInstanceProfileTags",
  ]

  # Security group changes, for the revert and demo developer roles.
  sg_rule_actions = [
    "ec2:AuthorizeSecurityGroupIngress",
    "ec2:AuthorizeSecurityGroupEgress",
    "ec2:RevokeSecurityGroupIngress",
    "ec2:RevokeSecurityGroupEgress",
    "ec2:ModifySecurityGroupRules",
    "ec2:UpdateSecurityGroupRuleDescriptionsIngress",
    "ec2:UpdateSecurityGroupRuleDescriptionsEgress",
  ]

  ec2_arn  = "arn:${local.partition}:ec2:${var.aws_region}:${local.account_id}"
  eks_arn  = "arn:${local.partition}:eks:${var.aws_region}:${local.account_id}"
  sqs_arn  = "arn:${local.partition}:sqs:${var.aws_region}:${local.account_id}"
  logs_arn = "arn:${local.partition}:logs:${var.aws_region}:${local.account_id}"

  # EKS clusters named ct-* and everything that hangs off them.
  project_eks_arns = [
    "${local.eks_arn}:cluster/ct-*",
    "${local.eks_arn}:nodegroup/ct-*/*",
    "${local.eks_arn}:addon/ct-*/*",
    "${local.eks_arn}:access-entry/ct-*/*",
    "${local.eks_arn}:podidentityassociation/ct-*/*",
  ]
  project_bucket_arns = [
    "arn:${local.partition}:s3:::ct-*",
    "arn:${local.partition}:s3:::demo-app-*",
  ]
  project_queue_arns = [
    "${local.sqs_arn}:ct-*",
    "${local.sqs_arn}:demo-app-*",
  ]
  project_events_arns = [
    "arn:${local.partition}:events:${var.aws_region}:${local.account_id}:rule/ct-*",
    "arn:${local.partition}:events:${var.aws_region}:${local.account_id}:event-bus/ct-*",
  ]
  project_trail_arns = ["arn:${local.partition}:cloudtrail:${var.aws_region}:${local.account_id}:trail/ct-*"]
  project_log_group_arns = [
    "${local.logs_arn}:log-group:/aws/eks/ct-*",
    "${local.logs_arn}:log-group:ct-*",
  ]
  project_budget_arns = ["arn:${local.partition}:budgets::${local.account_id}:budget/ct-*"]

  # The demo security group is found by its Name tag; terraform/demo-app/us-east-1 must set
  # Name = demo-app-sg-use1-<suffix>, because EC2 has no condition key for the group name.
  demo_sg_condition = {
    test     = "StringLike"
    variable = "aws:ResourceTag/Name"
    values   = ["demo-app-*"]
  }
}

################################################################################
# Permissions boundary: the most any project role can ever do
################################################################################

data "aws_iam_policy_document" "boundary" {
  # A boundary is a ceiling, not a grant: it names whole services so that each role's own
  # policy picks the actions and resources. Only services ConsoleTrack uses are listed.
  statement {
    sid = "ProjectServices"
    actions = [
      "ec2:*",
      "eks:*",
      "s3:*",
      "sqs:*",
      "events:*",
      "cloudtrail:*",
      "logs:*",
      "route53:*",
      "acm:*",
      "budgets:*",
      "sts:*",
    ]
    resources = ["*"]
  }

  # IAM reads are not path-scoped here: aws-mcp's IAM simulation and Terraform's refresh of
  # AWS-managed policy attachments read outside the project paths. Roles narrow it further.
  statement {
    sid       = "IamRead"
    actions   = ["iam:Get*", "iam:List*", "iam:SimulatePrincipalPolicy"]
    resources = ["*"]
  }

  statement {
    sid       = "IamWriteProjectPaths"
    actions   = concat(local.iam_write_actions, ["iam:PassRole"])
    resources = local.project_iam_arns
  }

  # EKS creates its service-linked roles on first use; their names and policies are AWS's.
  statement {
    sid       = "EksServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["eks.amazonaws.com", "eks-nodegroup.amazonaws.com", "spot.amazonaws.com"]
    }
  }

  statement {
    sid    = "DenyBoundaryChanges"
    effect = "Deny"
    actions = [
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]
    resources = [local.boundary_arn]
  }

  statement {
    sid    = "DenyOidcProviderChanges"
    effect = "Deny"
    actions = [
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:AddClientIDToOpenIDConnectProvider",
      "iam:RemoveClientIDFromOpenIDConnectProvider",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
    ]
    resources = ["*"]
  }

  statement {
    sid           = "DenyIamOutsideProjectPaths"
    effect        = "Deny"
    actions       = concat(local.iam_write_actions, ["iam:PassRole"])
    not_resources = local.project_iam_arns
  }

  # Every role a project role creates carries this same boundary, so no role can create a
  # more powerful one. A missing iam:PermissionsBoundary key also matches StringNotEquals.
  statement {
    sid       = "DenyRolesWithoutBoundary"
    effect    = "Deny"
    actions   = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
    resources = ["*"]

    condition {
      test     = "StringNotEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.boundary_arn]
    }
  }

  statement {
    sid       = "DenyBoundaryRemoval"
    effect    = "Deny"
    actions   = ["iam:DeleteRolePermissionsBoundary"]
    resources = ["*"]
  }

  statement {
    sid       = "DenyUsersAndKeys"
    effect    = "Deny"
    actions   = ["iam:CreateUser", "iam:CreateAccessKey"]
    resources = ["*"]
  }
}

module "boundary" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-policy"
  version = "6.8.2"

  name        = local.boundary_name
  path        = "/ct/"
  description = "ConsoleTrack permissions boundary: the most any project role can do"
  policy      = data.aws_iam_policy_document.boundary.json
}

################################################################################
# Shared policy pieces
################################################################################

# Refresh-only access for plan and apply: account-wide describe calls (the services don't
# support resource-level permissions for them), reads on project-named resources, the state
# bucket, and the S3-native lock files.
data "aws_iam_policy_document" "project_read" {
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]
  }

  statement {
    sid       = "StateRead"
    actions   = ["s3:GetObject"]
    resources = ["${local.state_bucket_arn}/*"]
  }

  statement {
    sid       = "StateLock"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_bucket_arn}/*.tflock"]
  }

  # Wildcard resource: these List/Describe calls are account-wide and accept only "*".
  statement {
    sid = "AccountWideDescribe"
    actions = [
      "ec2:Describe*",
      "ec2:GetSecurityGroupsForVpc",
      "eks:ListClusters",
      "eks:DescribeAddonVersions",
      "eks:DescribeClusterVersions",
      "sqs:ListQueues",
      "events:ListRules",
      "events:ListEventBuses",
      "cloudtrail:DescribeTrails",
      "cloudtrail:ListTrails",
      "logs:DescribeLogGroups",
      "route53:ListHostedZones",
      "route53:ListHostedZonesByName",
      "route53:GetHostedZoneCount",
      "acm:ListCertificates",
      "s3:ListAllMyBuckets",
      "iam:ListOpenIDConnectProviders",
    ]
    resources = ["*"]
  }

  statement {
    sid       = "EksRead"
    actions   = ["eks:Describe*", "eks:List*"]
    resources = local.project_eks_arns
  }

  # Bucket configuration only; object reads stay limited to the state bucket above.
  statement {
    sid = "S3BucketRead"
    actions = [
      "s3:GetBucket*",
      "s3:GetAccelerateConfiguration",
      "s3:GetEncryptionConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:GetReplicationConfiguration",
      "s3:GetIntelligentTieringConfiguration",
      "s3:GetAnalyticsConfiguration",
      "s3:GetInventoryConfiguration",
      "s3:GetMetricsConfiguration",
    ]
    resources = local.project_bucket_arns
  }

  statement {
    sid       = "SqsRead"
    actions   = ["sqs:GetQueueAttributes", "sqs:GetQueueUrl", "sqs:ListQueueTags", "sqs:ListDeadLetterSourceQueues"]
    resources = local.project_queue_arns
  }

  statement {
    sid       = "EventsRead"
    actions   = ["events:Describe*", "events:ListTargetsByRule", "events:ListTagsForResource"]
    resources = local.project_events_arns
  }

  statement {
    sid = "CloudTrailRead"
    actions = [
      "cloudtrail:GetTrail",
      "cloudtrail:GetTrailStatus",
      "cloudtrail:GetEventSelectors",
      "cloudtrail:GetInsightSelectors",
      "cloudtrail:ListTags",
    ]
    resources = local.project_trail_arns
  }

  statement {
    sid       = "LogsRead"
    actions   = ["logs:ListTagsForResource", "logs:ListTagsLogGroup"]
    resources = [for arn in local.project_log_group_arns : "${arn}*"]
  }

  # Route 53 zone IDs are random and the service has no tag conditions, so zone reads can't be
  # scoped to project zones. Reads only.
  statement {
    sid = "Route53Read"
    actions = [
      "route53:GetHostedZone",
      "route53:ListResourceRecordSets",
      "route53:ListTagsForResource",
      "route53:GetDNSSEC",
      "route53:GetChange",
    ]
    resources = [
      "arn:${local.partition}:route53:::hostedzone/*",
      "arn:${local.partition}:route53:::change/*",
    ]
  }

  statement {
    sid       = "AcmRead"
    actions   = ["acm:DescribeCertificate", "acm:GetCertificate", "acm:ListTagsForCertificate"]
    resources = ["arn:${local.partition}:acm:${var.aws_region}:${local.account_id}:certificate/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/project"
      values   = ["consoletrack"]
    }
  }

  statement {
    sid       = "BudgetsRead"
    actions   = ["budgets:ViewBudget", "budgets:ListTagsForResource"]
    resources = local.project_budget_arns
  }

  statement {
    sid       = "IamRead"
    actions   = local.iam_read_actions
    resources = concat(local.project_iam_arns, ["arn:${local.partition}:iam::aws:policy/*"])
  }

  statement {
    sid       = "OidcProviderRead"
    actions   = ["iam:GetOpenIDConnectProvider"]
    resources = [local.github_oidc_provider_arn]
  }
}

################################################################################
# GitHub OIDC provider and CI roles (enable_github_oidc)
################################################################################

module "github_oidc_provider" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-oidc-provider"
  version = "6.8.2"

  create = var.enable_github_oidc
  url    = "https://${local.github_oidc_host}"
}

# Plan role, used by pull request runs: read-only refresh plus the state lock.
data "aws_iam_policy_document" "gh_plan" {
  source_policy_documents = [data.aws_iam_policy_document.project_read.json]
}

# Apply role, used after merge behind the consoletrack-apply environment's approval. It can
# create, change, and delete resources named ct-*/demo-app-* or tagged project=consoletrack,
# and IAM only under /ct/ and /demo-app/ with the boundary attached. The boundary itself and
# the OIDC provider are out of its reach (the boundary denies them), so a human applies those.
data "aws_iam_policy_document" "gh_apply" {
  source_policy_documents = [data.aws_iam_policy_document.project_read.json]

  statement {
    sid       = "StateWrite"
    actions   = ["s3:PutObject"]
    resources = ["${local.state_bucket_arn}/*"]
  }

  # The state bucket matches ct-*, so keep the apply role from deleting it, purging old state
  # versions, or loosening its settings. Only scripts/bootstrap-state.sh manages it.
  statement {
    sid    = "ProtectStateBucket"
    effect = "Deny"
    actions = [
      "s3:DeleteBucket",
      "s3:DeleteBucketPolicy",
      "s3:DeleteObjectVersion",
      "s3:PutBucket*",
      "s3:PutEncryptionConfiguration",
      "s3:PutLifecycleConfiguration",
      "s3:PutReplicationConfiguration",
    ]
    resources = [local.state_bucket_arn, "${local.state_bucket_arn}/*"]
  }

  statement {
    sid       = "IamCreateRoleWithBoundary"
    actions   = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
    resources = local.project_role_arns

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.boundary_arn]
    }
  }

  statement {
    sid       = "IamWriteProjectPaths"
    actions   = setsubtract(local.iam_write_actions, ["iam:CreateRole", "iam:PutRolePermissionsBoundary"])
    resources = local.project_iam_arns
  }

  statement {
    sid       = "IamPassProjectRoles"
    actions   = ["iam:PassRole"]
    resources = local.project_role_arns
  }

  statement {
    sid       = "EksServiceLinkedRoles"
    actions   = ["iam:CreateServiceLinkedRole"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "iam:AWSServiceName"
      values   = ["eks.amazonaws.com", "eks-nodegroup.amazonaws.com", "spot.amazonaws.com"]
    }
  }

  # EC2 resources are matched by tag: provider default_tags put project=consoletrack on every
  # resource at creation, so creates must carry it and later changes must find it.
  statement {
    sid       = "Ec2CreateTagged"
    actions   = ["ec2:Create*", "ec2:RunInstances", "ec2:AllocateAddress", "ec2:ImportKeyPair"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/project"
      values   = ["consoletrack"]
    }
  }

  statement {
    sid       = "Ec2TagOnCreate"
    actions   = ["ec2:CreateTags"]
    resources = ["*"]

    condition {
      test     = "Null"
      variable = "ec2:CreateAction"
      values   = ["false"]
    }
  }

  statement {
    sid = "Ec2ChangeTagged"
    actions = [
      "ec2:Create*",
      "ec2:RunInstances",
      "ec2:Delete*",
      "ec2:Modify*",
      "ec2:Associate*",
      "ec2:Disassociate*",
      "ec2:Attach*",
      "ec2:Detach*",
      "ec2:Authorize*",
      "ec2:Revoke*",
      "ec2:Replace*",
      "ec2:Enable*",
      "ec2:Disable*",
      "ec2:UpdateSecurityGroupRuleDescriptions*",
      "ec2:StartInstances",
      "ec2:StopInstances",
      "ec2:TerminateInstances",
      "ec2:ReleaseAddress",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/project"
      values   = ["consoletrack"]
    }
  }

  # Inputs to RunInstances and launch templates that ConsoleTrack doesn't own (public AMIs and
  # their snapshots), and new security group rules, which have no tags until created.
  statement {
    sid     = "Ec2UntaggedInputs"
    actions = ["ec2:RunInstances", "ec2:Authorize*", "ec2:Revoke*"]
    resources = [
      "arn:${local.partition}:ec2:${var.aws_region}::image/*",
      "arn:${local.partition}:ec2:${var.aws_region}::snapshot/*",
      "${local.ec2_arn}:security-group-rule/*",
    ]
  }

  statement {
    sid       = "EksProjectClusters"
    actions   = ["eks:*"]
    resources = local.project_eks_arns
  }

  statement {
    sid       = "S3ProjectBuckets"
    actions   = ["s3:*"]
    resources = concat(local.project_bucket_arns, [for arn in local.project_bucket_arns : "${arn}/*"])
  }

  statement {
    sid       = "SqsProjectQueues"
    actions   = ["sqs:*"]
    resources = local.project_queue_arns
  }

  statement {
    sid       = "EventsProjectRules"
    actions   = ["events:*"]
    resources = local.project_events_arns
  }

  statement {
    sid       = "CloudTrailProjectTrails"
    actions   = ["cloudtrail:*"]
    resources = local.project_trail_arns
  }

  statement {
    sid       = "LogsProjectGroups"
    actions   = ["logs:*"]
    resources = concat(local.project_log_group_arns, [for arn in local.project_log_group_arns : "${arn}:*"])
  }

  statement {
    sid       = "AcmRequestTagged"
    actions   = ["acm:RequestCertificate", "acm:AddTagsToCertificate"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/project"
      values   = ["consoletrack"]
    }
  }

  statement {
    sid = "AcmChangeTagged"
    actions = [
      "acm:DeleteCertificate",
      "acm:AddTagsToCertificate",
      "acm:RemoveTagsFromCertificate",
      "acm:UpdateCertificateOptions",
    ]
    resources = ["arn:${local.partition}:acm:${var.aws_region}:${local.account_id}:certificate/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/project"
      values   = ["consoletrack"]
    }
  }

  statement {
    sid       = "BudgetsProject"
    actions   = ["budgets:ModifyBudget", "budgets:TagResource", "budgets:UntagResource"]
    resources = local.project_budget_arns
  }

  # Route 53 can't scope zones by name or tag (zone IDs are random), so record changes are
  # limited by record name instead: only DOMAIN and names under it, which covers the zone's own
  # records, ACM validation records, and the NS delegation in a route53 parent zone. Deleting a
  # zone is left to a human (`make domain-destroy`), so the apply role can't remove the parent.
  dynamic "statement" {
    for_each = var.domain != "" ? [var.domain] : []

    content {
      sid       = "Route53DomainRecords"
      actions   = ["route53:ChangeResourceRecordSets"]
      resources = ["arn:${local.partition}:route53:::hostedzone/*"]

      condition {
        test     = "ForAllValues:StringLike"
        variable = "route53:ChangeResourceRecordSetsNormalizedRecordNames"
        values   = [statement.value, "*.${statement.value}"]
      }
    }
  }

  dynamic "statement" {
    for_each = var.domain != "" ? [var.domain] : []

    content {
      sid       = "Route53Zones"
      actions   = ["route53:CreateHostedZone", "route53:ChangeTagsForResource"]
      resources = ["arn:${local.partition}:route53:::hostedzone/*"]
    }
  }
}

# Revert role, used by revert.yaml behind the consoletrack-revert environment: exactly what
# terraform/demo-app/us-east-1 manages, and only that root's state.
data "aws_iam_policy_document" "gh_revert" {
  # Plain ListBucket: `terraform init` lists workspaces without the root's prefix. It shows key
  # names only, never state contents.
  statement {
    sid       = "StateList"
    actions   = ["s3:ListBucket"]
    resources = [local.state_bucket_arn]
  }

  statement {
    sid       = "DemoAppState"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${local.state_bucket_arn}/demo-app/us-east-1/terraform.tfstate"]
  }

  statement {
    sid       = "DemoAppStateLock"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${local.state_bucket_arn}/demo-app/us-east-1/terraform.tfstate.tflock"]
  }

  statement {
    sid       = "Describe"
    actions   = ["ec2:Describe*", "s3:ListAllMyBuckets"]
    resources = ["*"]
  }

  statement {
    sid       = "DemoAppIamRead"
    actions   = local.iam_read_actions
    resources = concat(local.demo_app_iam_arns, ["arn:${local.partition}:iam::aws:policy/*"])
  }

  statement {
    sid       = "DemoAppIamCreateRoleWithBoundary"
    actions   = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
    resources = ["${local.iam_arn}:role/demo-app/demo-app-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [local.boundary_arn]
    }
  }

  statement {
    sid = "DemoAppIamWrite"
    actions = setsubtract(
      [for a in local.iam_write_actions : a if !strcontains(a, "InstanceProfile")],
      ["iam:CreateRole", "iam:PutRolePermissionsBoundary"],
    )
    resources = local.demo_app_iam_arns
  }

  # CreatePodIdentityAssociation passes the demo app's role to EKS Pod Identity.
  statement {
    sid       = "DemoAppPassRole"
    actions   = ["iam:PassRole"]
    resources = ["${local.iam_arn}:role/demo-app/demo-app-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["pods.eks.amazonaws.com"]
    }
  }

  statement {
    sid     = "DemoAppBucket"
    actions = ["s3:*"]
    resources = [
      "arn:${local.partition}:s3:::demo-app-*",
      "arn:${local.partition}:s3:::demo-app-*/*",
    ]
  }

  statement {
    sid       = "DemoSecurityGroupCreate"
    actions   = ["ec2:CreateSecurityGroup"]
    resources = ["${local.ec2_arn}:security-group/*"]

    condition {
      test     = "StringLike"
      variable = "aws:RequestTag/Name"
      values   = ["demo-app-*"]
    }
  }

  statement {
    sid       = "DemoSecurityGroupVpc"
    actions   = ["ec2:CreateSecurityGroup"]
    resources = ["${local.ec2_arn}:vpc/*"]
  }

  statement {
    sid       = "DemoSecurityGroupChange"
    actions   = concat(local.sg_rule_actions, ["ec2:DeleteSecurityGroup", "ec2:CreateTags", "ec2:DeleteTags"])
    resources = ["${local.ec2_arn}:security-group/*"]

    condition {
      test     = local.demo_sg_condition.test
      variable = local.demo_sg_condition.variable
      values   = local.demo_sg_condition.values
    }
  }

  # Rule resources are authorized alongside their group, which the statement above limits to
  # the demo group; rules created in the console carry no tags to match on.
  statement {
    sid       = "DemoSecurityGroupRules"
    actions   = concat(local.sg_rule_actions, ["ec2:CreateTags", "ec2:DeleteTags"])
    resources = ["${local.ec2_arn}:security-group-rule/*"]
  }

  statement {
    sid       = "TagOnCreate"
    actions   = ["ec2:CreateTags"]
    resources = ["${local.ec2_arn}:security-group/*", "${local.ec2_arn}:security-group-rule/*"]

    condition {
      test     = "StringEquals"
      variable = "ec2:CreateAction"
      values   = ["CreateSecurityGroup", "AuthorizeSecurityGroupIngress", "AuthorizeSecurityGroupEgress"]
    }
  }

  statement {
    sid       = "ClusterLookup"
    actions   = ["eks:DescribeCluster"]
    resources = ["${local.eks_arn}:cluster/${local.cluster_name}"]
  }

  statement {
    sid = "DemoAppPodIdentity"
    actions = [
      "eks:CreatePodIdentityAssociation",
      "eks:DeletePodIdentityAssociation",
      "eks:DescribePodIdentityAssociation",
      "eks:UpdatePodIdentityAssociation",
      "eks:ListPodIdentityAssociations",
      "eks:TagResource",
      "eks:UntagResource",
    ]
    resources = [
      "${local.eks_arn}:cluster/${local.cluster_name}",
      "${local.eks_arn}:podidentityassociation/${local.cluster_name}/*",
    ]
  }
}

# Deploy role, used by deploy.yaml: find the cluster endpoint. Kubernetes access comes from
# an EKS access entry in terraform/consoletrack/us-east-1, not from IAM.
data "aws_iam_policy_document" "gh_deploy" {
  statement {
    sid       = "DescribeProjectCluster"
    actions   = ["eks:DescribeCluster"]
    resources = ["${local.eks_arn}:cluster/${local.cluster_name}"]
  }
}

locals {
  github_role_policies = {
    plan   = data.aws_iam_policy_document.gh_plan.json
    apply  = data.aws_iam_policy_document.gh_apply.json
    revert = data.aws_iam_policy_document.gh_revert.json
    deploy = data.aws_iam_policy_document.gh_deploy.json
  }
}

# Trust: this repo's one GitHub environment per role, StringEquals on sub (no wildcards) and on
# aud. The document is written here and passed to the iam-role submodule instead of using its
# enable_github_oidc statement, whose aud check is ForAllValues:StringEquals: that also passes
# when the key is missing. Referencing the provider ARN orders each role after the provider.
data "aws_iam_policy_document" "github_trust" {
  for_each = var.enable_github_oidc ? local.github_role_policies : {}

  statement {
    sid     = "GithubOidc"
    actions = ["sts:AssumeRoleWithWebIdentity", "sts:TagSession"]

    principals {
      type        = "Federated"
      identifiers = [module.github_oidc_provider.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_host}:sub"
      values   = ["repo:${var.github_repo}:environment:consoletrack-${each.key}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc_host}:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Workflows set role-session-name: gh-${{ github.run_id }}, and the EventBridge rule skips
    # pipeline calls by that prefix, so a run can't assume these roles under any other name.
    condition {
      test     = "StringLike"
      variable = "sts:RoleSessionName"
      values   = ["gh-*"]
    }
  }
}

module "github_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  for_each = var.enable_github_oidc ? local.github_role_policies : {}

  name                 = "${local.name_prefix}-gh-${each.key}-${var.name_suffix}"
  use_name_prefix      = false
  path                 = "/ct/"
  description          = "ConsoleTrack GitHub Actions ${each.key} role (environment consoletrack-${each.key})"
  max_session_duration = 3600
  permissions_boundary = module.boundary.arn

  source_trust_policy_documents = [data.aws_iam_policy_document.github_trust[each.key].json]

  create_inline_policy           = true
  source_inline_policy_documents = [each.value]
}

################################################################################
# Demo developer role: simulated ClickOps for `make demo-drift` and `make demo-identity`
################################################################################

# Without operator_arn, resolve the caller's assumed-role session (an SSO login, for example)
# to the role it came from.
data "aws_iam_session_context" "operator" {
  count = var.operator_arn == "" ? 1 : 0

  arn = data.aws_caller_identity.current.arn
}

locals {
  operator_arn = var.operator_arn != "" ? var.operator_arn : data.aws_iam_session_context.operator[0].issuer_arn
}

data "aws_iam_policy_document" "demo_developer_trust" {
  statement {
    sid     = "Operator"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "AWS"
      identifiers = [local.operator_arn]
    }

    # The intake skips sessions named gh-*, and these changes must be seen.
    condition {
      test     = "StringNotLike"
      variable = "sts:RoleSessionName"
      values   = ["gh-*"]
    }
  }
}

data "aws_iam_policy_document" "demo_developer" {
  statement {
    sid       = "Describe"
    actions   = ["ec2:Describe*", "iam:ListPolicies", "iam:ListRoles"]
    resources = ["*"]
  }

  statement {
    sid       = "DemoAppIamRead"
    actions   = local.iam_read_actions
    resources = local.demo_app_iam_arns
  }

  # Edit the demo app's permissions in place, as someone in the console would: a new version
  # of its managed policy, or its inline policy. No new roles, attachments, or trust changes.
  statement {
    sid       = "DemoAppPolicyEdit"
    actions   = ["iam:CreatePolicyVersion", "iam:DeletePolicyVersion", "iam:SetDefaultPolicyVersion"]
    resources = ["${local.iam_arn}:policy/demo-app/demo-app-*"]
  }

  statement {
    sid       = "DemoAppInlinePolicyEdit"
    actions   = ["iam:PutRolePolicy", "iam:DeleteRolePolicy"]
    resources = ["${local.iam_arn}:role/demo-app/demo-app-*"]
  }

  statement {
    sid       = "DemoSecurityGroupRules"
    actions   = local.sg_rule_actions
    resources = ["${local.ec2_arn}:security-group/*"]

    condition {
      test     = local.demo_sg_condition.test
      variable = local.demo_sg_condition.variable
      values   = local.demo_sg_condition.values
    }
  }

  statement {
    sid       = "DemoSecurityGroupRuleResources"
    actions   = local.sg_rule_actions
    resources = ["${local.ec2_arn}:security-group-rule/*"]
  }
}

module "demo_developer_role" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role"
  version = "6.8.2"

  name                 = "${local.name_prefix}-demo-developer-${var.name_suffix}"
  use_name_prefix      = false
  path                 = "/ct/"
  description          = "ConsoleTrack demo developer: simulated console changes to the demo app"
  max_session_duration = 3600
  permissions_boundary = module.boundary.arn

  source_trust_policy_documents = [data.aws_iam_policy_document.demo_developer_trust.json]

  create_inline_policy           = true
  source_inline_policy_documents = [data.aws_iam_policy_document.demo_developer.json]
}
