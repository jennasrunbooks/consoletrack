# Two AZs. Nodes run in private subnets; public subnets hold only the NAT gateway and, with
# EXPOSURE=alb, the ALB. No interface endpoints: nodes need internet egress for the Claude and
# GitHub APIs anyway, so the NAT gateway carries ECR, STS, and SQS traffic too.

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, 2)
  # /16 → private /20s at the bottom of the range, public /24s well above them.
  private_subnets = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 4, i)]
  public_subnets  = [for i, _ in local.azs : cidrsubnet(var.vpc_cidr, 8, 48 + i)]
}

module "vpc" {
  source  = "terraform-aws-modules/vpc/aws"
  version = "6.7.3"

  name = "${local.name_prefix}-vpc-${local.name_tail}"
  cidr = var.vpc_cidr
  azs  = local.azs

  private_subnets = local.private_subnets
  public_subnets  = local.public_subnets

  # One managed NAT gateway for both AZs: about $0.045/hr plus $0.045/GB. If its AZ fails,
  # the other AZ's nodes lose egress, which is acceptable for a demo. Production uses one NAT
  # gateway per AZ (product-spec.md → Production hardening path, step 1).
  enable_nat_gateway     = true
  single_nat_gateway     = true
  one_nat_gateway_per_az = false

  enable_dns_hostnames = true
  enable_dns_support   = true

  # Where the Load Balancer Controller may place internet-facing and internal load balancers.
  public_subnet_tags  = { "kubernetes.io/role/elb" = "1" }
  private_subnet_tags = { "kubernetes.io/role/internal-elb" = "1" }
}

# Free S3 gateway endpoint on the private route tables: ECR image layers, the state bucket,
# CloudTrail logs, and the demo bucket skip the NAT gateway's per-GB charge.
module "vpc_endpoints" {
  source  = "terraform-aws-modules/vpc/aws//modules/vpc-endpoints"
  version = "6.7.3"

  vpc_id                = module.vpc.vpc_id
  create_security_group = false

  endpoints = {
    s3 = {
      service         = "s3"
      service_type    = "Gateway"
      route_table_ids = module.vpc.private_route_table_ids
      tags            = { Name = "${local.name_prefix}-s3-endpoint-${local.name_tail}" }
    }
  }
}
