output "state_bucket" {
  description = "Terraform state bucket, created by scripts/bootstrap-state.sh."
  value       = local.state_bucket
}

# Sensitive because the ARN contains the account ID; without this, only CI's log masking
# would hide it.
output "boundary_arn" {
  description = "ARN of the ct permissions boundary. Every project role must carry it."
  value       = module.boundary.arn
  sensitive   = true
}

# Sensitive because the ARNs contain the account ID. `make gh-secrets` reads this map and stores
# each ARN as its environment's AWS_ROLE_ARN secret.
output "github_env_role_arns" {
  description = "GitHub environment name (consoletrack-plan, -apply, -revert, -deploy) to its OIDC role ARN. {} when enable_github_oidc is false."
  value       = { for k, role in module.github_role : "consoletrack-${k}" => role.arn }
  sensitive   = true
}

output "demo_developer_role_arn" {
  description = "Role that `make demo-drift` and `make demo-identity` assume."
  value       = module.demo_developer_role.arn
  sensitive   = true
}

output "zone_id" {
  description = "Route 53 zone ID for DOMAIN, or null when DOMAIN is empty."
  value       = local.create_zone ? module.zone.id : null
}

output "name_servers" {
  description = "The zone's four name servers, or null when DOMAIN is empty."
  value       = local.create_zone ? module.zone.name_servers : null
}

output "domain_ns_instructions" {
  description = "The NS records that delegate DOMAIN from its parent zone."
  value = !local.create_zone ? null : (
    var.dns_parent == "route53"
    ? "Terraform added the NS records for ${local.domain_host} in the ${local.parent_domain} zone."
    : join("\n", concat(
      ["Add these NS records at the DNS provider for ${local.parent_domain} (host ${local.domain_host}, type NS):"],
      [for ns in coalesce(module.zone.name_servers, []) : "  ${local.domain_host}  NS  ${ns}"],
    ))
  )
}
