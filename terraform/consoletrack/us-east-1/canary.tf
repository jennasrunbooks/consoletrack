# The canary CronJob (Helm + deploy issue) tags this group every 15 minutes as ct-canary; the
# write travels CloudTrail → EventBridge → SQS like a real change, proving the intake end to
# end (product-spec.md → Intake health and canary).
#
# Raw resource: ignore_changes on one tag can't be set through a module. No rules (Terraform
# removes AWS's default egress rule) and attached to nothing.
resource "aws_security_group" "canary" {
  name        = "${local.name_prefix}-canary-sg-${local.name_tail}"
  description = "ConsoleTrack intake canary: no rules, attached to nothing"
  vpc_id      = module.vpc.vpc_id

  tags = {
    Name = "${local.name_prefix}-canary-sg-${local.name_tail}"
  }

  # The canary's own writes are never drift.
  lifecycle {
    ignore_changes = [tags["ct:canary"], tags_all["ct:canary"]]
  }
}
