provider "aws" {
  region = var.aws_region

  # Same tags as the global root. owner is NAME_SUFFIX, never the caller's identity (#3).
  default_tags {
    tags = {
      project    = "consoletrack"
      owner      = var.name_suffix
      managed_by = "terraform"
    }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  name_prefix = "ct"
  account_id  = data.aws_caller_identity.current.account_id
  partition   = data.aws_partition.current.partition

  # Regional names: ct-<component>-<region_code>-<suffix>. This root is us-east-1 only
  # (aws_region's validation), so the map has one entry.
  region_codes = { "us-east-1" = "use1" }
  region_code  = local.region_codes[var.aws_region]
  # "-use1-<suffix>", appended to every name in this root.
  name_tail = "${local.region_code}-${var.name_suffix}"

  # Must match local.cluster_name in terraform/consoletrack/global: the revert and deploy roles
  # are scoped to it.
  cluster_name = "${local.name_prefix}-eks-${local.name_tail}"

  iam_arn = "arn:${local.partition}:iam::${local.account_id}"
  # Created by terraform/consoletrack/global; every role here carries it.
  boundary_arn = "${local.iam_arn}:policy/ct/${local.name_prefix}-boundary-${var.name_suffix}"

  # Created by scripts/bootstrap-state.sh. aws-mcp reads the demo-app root's state object.
  state_bucket_arn = "arn:${local.partition}:s3:::${local.name_prefix}-tfstate-${var.name_suffix}"
}
