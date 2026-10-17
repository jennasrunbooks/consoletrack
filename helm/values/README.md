# helm/values

Values files for the third-party releases in `helm/helmfile.yaml.gotmpl` (`tier=addons`):
kube-prometheus-stack, cloudnative-pg, the AWS Load Balancer Controller (`EXPOSURE=alb` only),
and ExternalDNS (`DOMAIN` only). Helmfile installs them with `make addons`; Terraform installs
nothing inside Kubernetes.

Filled by [#12](https://github.com/jennasrunbooks/consoletrack/issues/12) (Helm charts, Helmfile, and `make up`/`down`/`deploy`).
