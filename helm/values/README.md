# helm/values

Values files for the third-party releases in `helm/helmfile.yaml.gotmpl` (`tier=addons`):
kube-prometheus-stack, cloudnative-pg, the AWS Load Balancer Controller (`EXPOSURE=alb` only),
and ExternalDNS (`DOMAIN` only). Helmfile installs them with `make addons`; Terraform installs
nothing inside Kubernetes.

Filled by the EKS + deploy pipeline workstream (no issue filed yet).
