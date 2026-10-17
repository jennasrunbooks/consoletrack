# Public zone for DOMAIN only (product-spec.md → Custom domain). The parent domain and its
# other records never move. The zone lives here, not in us-east-1, so it survives `make down`
# and the ACM certificate re-validates quickly on the next `make up`.

locals {
  create_zone   = var.domain != ""
  domain_labels = split(".", var.domain)
  # consoletrack.example.com → host "consoletrack" in parent "example.com".
  domain_host   = local.domain_labels[0]
  parent_domain = join(".", slice(local.domain_labels, 1, length(local.domain_labels)))
}

module "zone" {
  source  = "terraform-aws-modules/route53/aws"
  version = "6.5.1"

  create  = local.create_zone
  name    = var.domain
  comment = "ConsoleTrack dashboard (${var.name_suffix})"
}

# DNS_PARENT=route53: the parent zone is in this account, so delegate to the new zone here.
# With external (the default) the NS records are only output, for adding at the DNS provider.
module "parent_delegation" {
  source  = "terraform-aws-modules/route53/aws"
  version = "6.5.1"

  create      = local.create_zone && var.dns_parent == "route53"
  create_zone = false
  name        = local.parent_domain

  records = {
    (local.domain_host) = {
      type    = "NS"
      ttl     = 300
      records = module.zone.name_servers
    }
  }
}
