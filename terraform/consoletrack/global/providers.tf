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

  # Short region code for regional resource names: ct-<component>-<region_code>-<suffix>.
  # Global resources (IAM, the budget, the state bucket) keep ct-<component>-<suffix>.
  # aws_region's validation rejects any region missing here.
  region_codes = {
    "us-east-1"      = "use1"
    "us-east-2"      = "use2"
    "us-west-1"      = "usw1"
    "us-west-2"      = "usw2"
    "ca-central-1"   = "cac1"
    "sa-east-1"      = "sae1"
    "eu-west-1"      = "euw1"
    "eu-west-2"      = "euw2"
    "eu-west-3"      = "euw3"
    "eu-central-1"   = "euc1"
    "eu-north-1"     = "eun1"
    "ap-south-1"     = "aps1"
    "ap-northeast-1" = "apne1"
    "ap-northeast-2" = "apne2"
    "ap-southeast-1" = "apse1"
    "ap-southeast-2" = "apse2"
  }
  region_code = local.region_codes[var.aws_region]

  # Created by terraform/consoletrack/us-east-1, which must use this name: the revert and deploy
  # roles are scoped to it.
  cluster_name = "${local.name_prefix}-eks-${local.region_code}-${var.name_suffix}"
}
