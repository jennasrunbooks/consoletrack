################################################################################
# Cluster admin: the operator, resolved like the global root's demo developer trust
################################################################################

# Without operator_arn, resolve the caller's assumed-role session (an SSO login, for example)
# to the role it came from.
data "aws_iam_session_context" "operator" {
  count = var.operator_arn == "" ? 1 : 0

  arn = data.aws_caller_identity.current.arn
}

locals {
  operator_arn = var.operator_arn != "" ? var.operator_arn : data.aws_iam_session_context.operator[0].issuer_arn

  eks_public_access_cidrs = [for c in split(",", var.eks_public_access_cidrs) : trimspace(c)]

  # CI's deploy role (global root) maps to this Kubernetes group. The Helm + deploy issue binds
  # it with RoleBindings in the consoletrack and demo namespaces only; it has no ClusterRole.
  deploy_role_arn         = "${local.iam_arn}:role/ct/${local.name_prefix}-gh-deploy-${var.name_suffix}"
  deploy_kubernetes_group = "consoletrack-deployers"

  operator_access_entry = {
    operator = {
      principal_arn = local.operator_arn
      policy_associations = {
        admin = {
          policy_arn   = "arn:${local.partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy"
          access_scope = { type = "cluster" }
        }
      }
    }
  }
  deploy_access_entry = var.enable_github_oidc ? {
    gh_deploy = {
      principal_arn     = local.deploy_role_arn
      kubernetes_groups = [local.deploy_kubernetes_group]
    }
  } : {}
}

################################################################################
# Cluster, managed add-ons, node group
################################################################################

module "eks" {
  source  = "terraform-aws-modules/eks/aws"
  version = "21.29.0"

  name               = local.cluster_name
  kubernetes_version = var.k8s_version
  # STANDARD: the cluster is never silently moved to extended support ($0.60/hr).
  upgrade_policy = { support_type = "STANDARD" }

  # Access entries only (no aws-auth ConfigMap); Pod Identity instead of IRSA.
  authentication_mode                      = "API"
  enable_irsa                              = false
  enable_cluster_creator_admin_permissions = false
  access_entries                           = merge(local.operator_access_entry, local.deploy_access_entry)

  # Trade-off: the public endpoint stays open (IAM authentication still required) because
  # GitHub-hosted runners deploy through it and their address ranges can't be listed in
  # EKS's 40-CIDR limit. Nodes use the private endpoint. Narrowing it is one variable
  # (EKS_PUBLIC_ACCESS_CIDRS); turning it off is production hardening step 4.
  endpoint_public_access       = true
  endpoint_private_access      = true
  endpoint_public_access_cidrs = local.eks_public_access_cidrs

  # No customer-managed KMS key: EKS's default envelope encryption (AWS-owned key) covers
  # Kubernetes API data, including Secrets.
  encryption_config = null
  create_kms_key    = false

  # Control plane logs off: CloudWatch ingestion is the one cost here that grows with use.
  # Turn on "audit" when you need it.
  enabled_log_types           = []
  create_cloudwatch_log_group = false

  vpc_id     = module.vpc.vpc_id
  subnet_ids = module.vpc.private_subnets

  iam_role_name                 = "${local.name_prefix}-eks-cluster-${local.name_tail}"
  iam_role_use_name_prefix      = false
  iam_role_path                 = "/ct/"
  iam_role_permissions_boundary = local.boundary_arn

  # before_compute: the Pod Identity agent and the CNI (which gets its credentials from Pod
  # Identity) must be in place before the first node joins.
  addons = {
    eks-pod-identity-agent = {
      before_compute = true
    }
    vpc-cni = {
      before_compute = true
      pod_identity_association = [{
        role_arn        = module.vpc_cni_pod_identity.iam_role_arn
        service_account = "aws-node"
      }]
    }
    kube-proxy = {}
    coredns    = {}
    aws-ebs-csi-driver = {
      pod_identity_association = [{
        role_arn        = module.ebs_csi_pod_identity.iam_role_arn
        service_account = "ebs-csi-controller-sa"
      }]
    }
  }

  eks_managed_node_groups = {
    default = {
      name            = "${local.name_prefix}-nodes-${local.name_tail}"
      use_name_prefix = false

      ami_type       = "AL2023_ARM_64_STANDARD"
      instance_types = [var.node_instance_type]
      capacity_type  = var.use_spot ? "SPOT" : "ON_DEMAND"

      min_size     = 1
      max_size     = 3
      desired_size = 2

      subnet_ids = module.vpc.private_subnets

      # Worker node policy and ECR read-only only. CNI and EBS have their own Pod Identity
      # roles, and pods can't reach IMDS (hop limit 1), so they never use these credentials.
      iam_role_name                 = "${local.name_prefix}-eks-node-${local.name_tail}"
      iam_role_use_name_prefix      = false
      iam_role_path                 = "/ct/"
      iam_role_permissions_boundary = local.boundary_arn
      iam_role_attach_cni_policy    = false
    }
  }
}
