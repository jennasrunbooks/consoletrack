# Outputs whose values contain the account ID are sensitive, as in the global root, so plans
# and CI logs don't print it.

output "vpc_id" {
  description = "VPC ID. terraform/demo-app/us-east-1 finds the VPC by its Name tag instead."
  value       = module.vpc.vpc_id
}

output "vpc_name" {
  description = "VPC Name tag (ct-vpc-use1-<suffix>), for data source lookups."
  value       = "${local.name_prefix}-vpc-${local.name_tail}"
}

output "private_subnet_ids" {
  description = "Private subnets, where the nodes run."
  value       = module.vpc.private_subnets
}

output "cluster_name" {
  description = "EKS cluster name. Must equal local.cluster_name in the global root."
  value       = module.eks.cluster_name
}

output "deploy_kubernetes_group" {
  description = "Kubernetes group CI's deploy role maps to; bind it in the consoletrack and demo namespaces."
  value       = local.deploy_kubernetes_group
}

output "changes_queue_url" {
  description = "Change queue URL, read by the worker."
  value       = module.changes_queue.queue_url
  sensitive   = true
}

output "changes_queue_arn" {
  description = "Change queue ARN."
  value       = module.changes_queue.queue_arn
  sensitive   = true
}

output "changes_dlq_url" {
  description = "Dead-letter queue URL, read by the intake health check."
  value       = module.changes_queue.dead_letter_queue_url
  sensitive   = true
}

output "trail_name" {
  description = "CloudTrail trail name, for cloudtrail:GetTrailStatus."
  value       = local.trail_name
}

output "trail_arn" {
  description = "CloudTrail trail ARN."
  value       = module.trail.cloudtrail_arn
  sensitive   = true
}

output "changes_rule_arn" {
  description = "EventBridge rule ARN, the only sender the queue accepts."
  value       = module.changes_rule.eventbridge_rule_arns[local.rule_name]
  sensitive   = true
}

output "canary_security_group_id" {
  description = "Security group the canary tags."
  value       = aws_security_group.canary.id
}

output "pod_identity_role_arns" {
  description = "Pod Identity role ARN per component (lbc and external-dns only when enabled)."
  value = merge(
    {
      vpc-cni = module.vpc_cni_pod_identity.iam_role_arn
      ebs-csi = module.ebs_csi_pod_identity.iam_role_arn
    },
    { for k, m in module.app_pod_identity : k => m.iam_role_arn },
    var.exposure == "alb" ? { lbc = module.lbc_pod_identity.iam_role_arn } : {},
    var.domain != "" ? { external-dns = module.external_dns_pod_identity.iam_role_arn } : {},
  )
  sensitive = true
}

output "certificate_arn" {
  description = "ACM certificate for DOMAIN, or null when DOMAIN is empty."
  value       = var.domain != "" ? module.acm.acm_certificate_arn : null
  sensitive   = true
}
