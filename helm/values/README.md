# helm/values

Values files for the third-party releases in `helm/helmfile.yaml.gotmpl` (`tier=addons`).
Helmfile installs them with `make addons`; Terraform installs nothing inside Kubernetes. Files
ending in `.gotmpl` are templates over the environment values (`helm/environments/`).

| File | Release | Notable settings |
|---|---|---|
| `kube-prometheus-stack.yaml` | kube-prometheus-stack | 2-day, 1 GB retention on an emptyDir; Grafana on with the dashboard sidecar watching every namespace; ServiceMonitors, PrometheusRules, and AlertmanagerConfigs picked up from every namespace; AlertmanagerConfig matcher strategy `None`, so the consoletrack route also receives `demo` alerts; etcd, scheduler, controller-manager, and kube-proxy monitors off (EKS doesn't expose them) |
| `kube-prometheus-stack-eks.yaml` | kube-prometheus-stack (`eks` only) | Tempo and Loki datasources with the local stack's UIDs and links; the default dashboards off (`grafana-dashboards` ships the kept ones); Incident journey as the home dashboard; Prometheus remote-write receiver on for the collector |
| `tempo.yaml` | tempo (`eks`) | Single binary, emptyDir at `/var/tempo`, 24 h retention, the Compose image's digest, hardened, no token |
| `loki.yaml` | loki (`eks`) | `deploymentMode: Monolithic`, filesystem on an emptyDir, 24 h retention; no gateway, canary, caches, MinIO, rules sidecar, or ClusterRole |
| `otel-collector.yaml.gotmpl` | otel-collector (`eks`) | Deployment; config read from `observability/otel-collector/config.yaml` + `kind.yaml` with only the endpoints changed; OTLP/HTTP only |
| `cloudnative-pg.yaml` | cloudnative-pg | One operator replica, small requests |
| `aws-load-balancer-controller.yaml.gotmpl` | aws-load-balancer-controller (`EXPOSURE=alb`) | Pod Identity service account `aws-load-balancer-controller`; region and VPC set explicitly; `enableWaf`, `enableWafv2`, `enableShield` false (the `ct` boundary has none of those APIs); ALB Gateway API on, NLB off |
| `external-dns.yaml.gotmpl` | external-dns (`DOMAIN`) | Pod Identity service account `external-dns`; source `gateway-httproute`; `DOMAIN` only; TXT registry with `txtOwnerId` = `NAME_SUFFIX` |

Pod Identity associations for both controllers are in `terraform/consoletrack/us-east-1`
(`pod-identity.tf`); the service account names here must match them.
