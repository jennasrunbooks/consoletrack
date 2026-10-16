provider "aws" {
  region = var.aws_region

  # owner is NAME_SUFFIX, never the caller's identity: a caller-based tag would flip between
  # the human and the CI role on every apply and would put the account ID in every plan (#3).
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

  # Created by scripts/bootstrap-state.sh, not by Terraform.
  state_bucket     = "${local.name_prefix}-tfstate-${var.name_suffix}"
  state_bucket_arn = "arn:${local.partition}:s3:::${local.state_bucket}"

  # Created by terraform/consoletrack/us-east-1, which must use this name: the revert and deploy
  # roles are scoped to it.
  cluster_name = "${local.name_prefix}-eks-${var.name_suffix}"
}
