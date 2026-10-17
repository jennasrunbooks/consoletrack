# DOMAIN only. The zone lives in terraform/consoletrack/global (it survives `make down`); it is
# looked up by name here, never through remote state. The certificate is re-issued on each
# `make up` and validates quickly because the zone and its delegation persist.

data "aws_route53_zone" "domain" {
  count = var.domain != "" ? 1 : 0

  name         = var.domain
  private_zone = false
}

module "acm" {
  source  = "terraform-aws-modules/acm/aws"
  version = "6.3.1"

  create_certificate = var.domain != ""

  domain_name       = var.domain
  zone_id           = try(data.aws_route53_zone.domain[0].zone_id, null)
  validation_method = "DNS"

  wait_for_validation = true
}
