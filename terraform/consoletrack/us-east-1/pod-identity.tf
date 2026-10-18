# One role per namespace:serviceaccount, each trusting pods.eks.amazonaws.com, under /ct/ with
# the boundary. The CNI and EBS CSI associations are made by their EKS add-ons (eks.tf), so
# they exist before the add-ons' pods start; the rest are made here.

locals {
  pod_identity_defaults = {
    path                     = "/ct/"
    permissions_boundary_arn = local.boundary_arn
  }

  queue_arn  = module.changes_queue.queue_arn
  dlq_arn    = module.changes_queue.dead_letter_queue_arn
  eks_arn    = "arn:${local.partition}:eks:${var.aws_region}:${local.account_id}"
  iam_paths  = [for p in split(",", var.watched_iam_paths) : trimspace(p)]
  canary_tag = "ct:canary"
}

################################################################################
# Cluster add-ons and controllers: the module's built-in policies
################################################################################

module "vpc_cni_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  name                     = "${local.name_prefix}-vpc-cni-${local.name_tail}"
  use_name_prefix          = false
  path                     = local.pod_identity_defaults.path
  permissions_boundary_arn = local.pod_identity_defaults.permissions_boundary_arn

  attach_aws_vpc_cni_policy = true
  aws_vpc_cni_enable_ipv4   = true
}

module "ebs_csi_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  name                     = "${local.name_prefix}-ebs-csi-${local.name_tail}"
  use_name_prefix          = false
  path                     = local.pod_identity_defaults.path
  permissions_boundary_arn = local.pod_identity_defaults.permissions_boundary_arn

  attach_aws_ebs_csi_policy = true
}

# EXPOSURE=alb only. The boundary has no WAF, Shield, or Cognito, so the controller's chart
# must set enableWaf, enableWafv2, and enableShield to false (Helm + deploy issue).
module "lbc_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  create = var.exposure == "alb"

  name                     = "${local.name_prefix}-lbc-${local.name_tail}"
  use_name_prefix          = false
  path                     = local.pod_identity_defaults.path
  permissions_boundary_arn = local.pod_identity_defaults.permissions_boundary_arn

  attach_aws_lb_controller_policy = true

  associations = {
    lbc = {
      cluster_name    = module.eks.cluster_name
      namespace       = "kube-system"
      service_account = "aws-load-balancer-controller"
    }
  }
}

# DOMAIN only: record changes in that one zone, list and read on zones.
module "external_dns_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  create = var.domain != ""

  name                     = "${local.name_prefix}-external-dns-${local.name_tail}"
  use_name_prefix          = false
  path                     = local.pod_identity_defaults.path
  permissions_boundary_arn = local.pod_identity_defaults.permissions_boundary_arn

  attach_external_dns_policy    = true
  external_dns_hosted_zone_arns = [for z in data.aws_route53_zone.domain : z.arn]

  associations = {
    external_dns = {
      cluster_name    = module.eks.cluster_name
      namespace       = "kube-system"
      service_account = "external-dns"
    }
  }
}

################################################################################
# ConsoleTrack's own pods: custom policies
################################################################################

data "aws_iam_policy_document" "aws_mcp" {
  # Account-wide read: LookupEvents has no resource-level permissions (accepted gap,
  # product-spec.md → Identity and access).
  statement {
    sid       = "CloudTrailLookup"
    actions   = ["cloudtrail:LookupEvents"]
    resources = ["*"]
  }

  # Account-wide read: DescribeSecurityGroups has no resource-level permissions (accepted gap).
  statement {
    sid       = "SecurityGroupRead"
    actions   = ["ec2:DescribeSecurityGroups"]
    resources = ["*"]
  }

  statement {
    sid = "IamReadWatchedPaths"
    actions = [
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:ListRoleTags",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListPolicyVersions",
      "iam:ListEntitiesForPolicy",
      "iam:SimulatePrincipalPolicy",
    ]
    resources = flatten([
      for p in local.iam_paths : ["${local.iam_arn}:role${p}*", "${local.iam_arn}:policy${p}*"]
    ])
  }

  statement {
    sid       = "PodIdentityRead"
    actions   = ["eks:DescribePodIdentityAssociation"]
    resources = ["${local.eks_arn}:podidentityassociation/${local.cluster_name}/*"]
  }

  # The demo-app root's state only, to map a changed resource to its Terraform address.
  statement {
    sid       = "DemoAppState"
    actions   = ["s3:GetObject"]
    resources = ["${local.state_bucket_arn}/demo-app/us-east-1/terraform.tfstate"]
  }
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "ConsumeChanges"
    actions   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility"]
    resources = [local.queue_arn]
  }

  # Backlog, oldest message age, and dead-letter count for the intake health endpoint.
  statement {
    sid       = "QueueHealth"
    actions   = ["sqs:GetQueueAttributes"]
    resources = [local.queue_arn, local.dlq_arn]
  }

  statement {
    sid       = "TrailHealth"
    actions   = ["cloudtrail:GetTrailStatus"]
    resources = [local.trail_arn]
  }
}

# Only the ct:canary tag, only on the canary security group. ForAllValues allows no other
# key; ForAnyValue rejects a request with no tag keys at all.
data "aws_iam_policy_document" "canary" {
  statement {
    sid       = "CanaryTag"
    actions   = ["ec2:CreateTags"]
    resources = [aws_security_group.canary.arn]

    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = [local.canary_tag]
    }

    condition {
      test     = "ForAnyValue:StringEquals"
      variable = "aws:TagKeys"
      values   = [local.canary_tag]
    }
  }
}

locals {
  app_pod_identities = {
    aws-mcp = { service_account = "aws-mcp", policy = data.aws_iam_policy_document.aws_mcp.json }
    worker  = { service_account = "worker", policy = data.aws_iam_policy_document.worker.json }
    canary  = { service_account = "canary", policy = data.aws_iam_policy_document.canary.json }
  }
}

module "app_pod_identity" {
  source  = "terraform-aws-modules/eks-pod-identity/aws"
  version = "2.9.0"

  for_each = local.app_pod_identities

  name                     = "${local.name_prefix}-${each.key}-${local.name_tail}"
  use_name_prefix          = false
  path                     = local.pod_identity_defaults.path
  permissions_boundary_arn = local.pod_identity_defaults.permissions_boundary_arn

  attach_custom_policy      = true
  custom_policy_description = "ConsoleTrack ${each.key}: see product-spec.md → Identity and access"
  source_policy_documents   = [each.value.policy]

  associations = {
    (each.key) = {
      cluster_name    = module.eks.cluster_name
      namespace       = "consoletrack"
      service_account = each.value.service_account
    }
  }
}
