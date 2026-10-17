# CloudTrail → EventBridge (default bus) → SQS: every out-of-band write becomes a message for
# the worker (product-spec.md → Workflow and data model → What counts as out-of-band).

locals {
  trail_name        = "${local.name_prefix}-trail-${local.name_tail}"
  trail_bucket_name = "${local.name_prefix}-trail-logs-${local.name_tail}"
  queue_name        = "${local.name_prefix}-changes-${local.name_tail}"
  dlq_name          = "${local.name_prefix}-changes-dlq-${local.name_tail}"
  rule_name         = "${local.name_prefix}-changes-${local.name_tail}"
  trail_arn         = "arn:${local.partition}:cloudtrail:${var.aws_region}:${local.account_id}:trail/${local.trail_name}"

  # patterns/changes.json is the whole rule, tested by tests/test_event_pattern.py. It keeps:
  #   write calls (readOnly false) to IAM, EC2, S3, KMS, and EKS;
  # and drops:
  #   pipeline sessions: ct-gh-* roles under a gh-<run id> session name (the roles' trust
  #   policies require that name, so a person can't hide behind it);
  #   AWS services acting on your behalf (userIdentity.invokedBy is set);
  #   ConsoleTrack's own platform roles: the EKS cluster and node roles (ct-eks-*), CNI, EBS
  #   CSI, Load Balancer Controller, and ExternalDNS. ct-canary is deliberately not listed:
  #   its tag write must arrive to prove the intake works.
  event_pattern = jsonencode(jsondecode(file("${path.module}/patterns/changes.json")))
}

################################################################################
# Trail and log bucket
################################################################################

data "aws_iam_policy_document" "trail_bucket_extra" {
  # The module's bucket policy lets any CloudTrail trail write; only this one may.
  statement {
    sid       = "OnlyThisTrail"
    effect    = "Deny"
    actions   = ["s3:PutObject"]
    resources = ["arn:${local.partition}:s3:::${local.trail_bucket_name}/*"]

    principals {
      type        = "Service"
      identifiers = ["cloudtrail.amazonaws.com", "config.amazonaws.com"]
    }

    condition {
      test     = "StringNotEquals"
      variable = "aws:SourceArn"
      values   = [local.trail_arn]
    }
  }
}

module "trail_bucket" {
  source  = "cloudposse/cloudtrail-s3-bucket/aws"
  version = "0.33.0"

  name   = local.trail_bucket_name
  acl    = "private"
  policy = data.aws_iam_policy_document.trail_bucket_extra.json

  # `make down` destroys this root with the logs still in it.
  force_destroy      = true
  versioning_enabled = false

  # The module's lifecycle always adds a 30-day Standard-IA transition, which S3 rejects
  # before a 14-day expiration, so the rule below replaces it.
  lifecycle_rule_enabled = false
}

# Raw resource: the module can't express a 14-day expiration (see above).
resource "aws_s3_bucket_lifecycle_configuration" "trail_logs" {
  bucket = module.trail_bucket.bucket_id

  rule {
    id     = "expire-14-days"
    status = "Enabled"

    filter {}

    expiration {
      days = 14
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

module "trail" {
  source  = "cloudposse/cloudtrail/aws"
  version = "0.24.0"

  name           = local.trail_name
  s3_bucket_name = module.trail_bucket.bucket_id

  # Single region, write management events only (the first copy is free), plus IAM and other
  # global services, whose events arrive in us-east-1.
  is_multi_region_trail         = false
  include_global_service_events = true
  enable_log_file_validation    = true
  enable_logging                = true

  event_selector = [{
    include_management_events = true
    read_write_type           = "WriteOnly"
    data_resource             = []
  }]

  # CreateTrail checks the bucket policy, which the bucket module writes separately.
  depends_on = [module.trail_bucket]
}

################################################################################
# Queue and rule
################################################################################

module "changes_queue" {
  source  = "terraform-aws-modules/sqs/aws"
  version = "5.2.2"

  name                    = local.queue_name
  sqs_managed_sse_enabled = true

  create_dlq                    = true
  dlq_name                      = local.dlq_name
  dlq_sqs_managed_sse_enabled   = true
  dlq_message_retention_seconds = 1209600 # 14 days, to inspect what the worker couldn't process
  redrive_policy                = { maxReceiveCount = 5 }

  create_queue_policy = true
  queue_policy_statements = {
    eventbridge = {
      sid     = "OnlyTheChangesRule"
      actions = ["sqs:SendMessage"]
      principals = [{
        type        = "Service"
        identifiers = ["events.amazonaws.com"]
      }]
      condition = [{
        test     = "ArnEquals"
        variable = "aws:SourceArn"
        values   = [module.changes_rule.eventbridge_rule_arns[local.rule_name]]
      }]
    }
  }
}

module "changes_rule" {
  source  = "terraform-aws-modules/eventbridge/aws"
  version = "4.3.2"

  create_bus          = false
  bus_name            = "default"
  create_role         = false # SQS targets use the queue policy, not a role
  append_rule_postfix = false
  # The module defaults to a CloudWatch log delivery on the bus; the default bus isn't ours.
  create_log_delivery_source = false
  create_log_delivery        = false

  rules = {
    (local.rule_name) = {
      description   = "ConsoleTrack: out-of-band AWS writes (patterns/changes.json)"
      event_pattern = local.event_pattern
    }
  }

  targets = {
    (local.rule_name) = [{
      name      = "changes-queue"
      target_id = "changes-queue"
      arn       = module.changes_queue.queue_arn
    }]
  }
}
