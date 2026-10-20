# helm

Everything inside Kubernetes. Terraform is AWS-only; Helmfile owns every chart, third-party
and ConsoleTrack's own (`product-spec.md` → Infrastructure as code).

```text
helm/
  helmfile.yaml.gotmpl    # repositories, every release (tier=addons|app labels), pinned chart versions, environments
  charts/
    consoletrack/         # our app chart
    demo-app/             # the demo workload (demo namespace)
    gateway-api-crds/     # pinned upstream Gateway API CRDs wrapped as a chart
    cluster-defaults/     # cluster-scoped objects no other chart creates (gp3 StorageClass, ALB GatewayClass)
  values/                 # values for third-party releases
  environments/           # eks.yaml (default), local.yaml (kind); values.generated.yaml from .env (gitignored)
  tests/                  # checks over the rendered manifests; placeholder values for `make helm-lint`
```

## Releases

| Release | Namespace | Chart | Tier | When | `needs` |
|---|---|---|---|---|---|
| `gateway-api-crds` | kube-system | `./charts/gateway-api-crds` (Gateway API v1.6.2) | addons | always | |
| `cluster-defaults` | kube-system | `./charts/cluster-defaults` | addons | always (StorageClass on `eks`, GatewayClass with `alb`) | gateway-api-crds |
| `kube-prometheus-stack` | monitoring | `prometheus-community/kube-prometheus-stack` 91.9.0 | addons | always | |
| `cloudnative-pg` | cnpg-system | `cnpg/cloudnative-pg` 0.29.1 | addons | always | |
| `aws-load-balancer-controller` | kube-system | `eks/aws-load-balancer-controller` 3.5.0 | addons | `exposure == "alb"` | gateway-api-crds, cluster-defaults |
| `external-dns` | kube-system | `external-dns/external-dns` 1.23.0 | addons | `domain != ""` | gateway-api-crds |
| `demo-app` | demo | `./charts/demo-app` | app | always | kube-prometheus-stack |
| `consoletrack` | consoletrack | `./charts/consoletrack` | app | always | gateway-api-crds, cluster-defaults, kube-prometheus-stack, cloudnative-pg, demo-app (and the Load Balancer Controller with `alb`) |

- `make addons` applies `tier=addons` (cluster-wide, so a human with cluster admin);
  `make deploy` and CI apply only `tier=app` (`helmfile -l tier=app apply`), whose objects are
  all namespaced. CI's deploy role can't apply anything else (`docs/permissions.md`).
- `consoletrack` needs `demo-app` because its Roles for `k8s-mcp`, the remediator, and the
  deployers go into the `demo` namespace, which the `demo-app` release creates.
- Every release waits for readiness and rolls back a failed upgrade (`helmDefaults`).
- Third-party chart versions are pinned in `helmfile.yaml.gotmpl` and bumped by hand: Dependabot
  only updates the local charts' `Chart.yaml` dependencies. Pick a release at least two weeks
  old that supports the cluster's Kubernetes version, and bump `gateway-api-crds` together with
  the Load Balancer Controller.

## Environments

Deployment targets, not apps: `eks` (also used when `-e` is left out) and `local` (kind, without
the Load Balancer Controller, ExternalDNS, or the canary). Each reads
`environments/<env>.yaml`, then `environments/values.generated.yaml`, which the Makefile writes
from `.env` and the Terraform outputs. The keys are listed in
[environments/README.md](environments/README.md).

On `eks` the `consoletrack` chart refuses to render without the Terraform outputs it needs
(queue URLs, trail, canary security group), and with `EXPOSURE=alb` without `DOMAIN`, the
certificate, and `ALLOWED_CIDRS`, so a half-configured deploy fails before it reaches the
cluster.

`make local` uses the `local` environment for two releases only:
`helmfile -e local -l name=kube-prometheus-stack -l name=demo-app apply` (`sync` without helm-diff), run by
`scripts/local-kind.sh`. Docker Compose runs Postgres, the backend, and the dashboard, so the
`consoletrack` release, CloudNativePG, and the Gateway API CRDs stay out of kind. From the
`consoletrack` chart, the script applies only the k8s-mcp RBAC, the ServiceAccounts, and the
AlertmanagerConfig. It renders them with `compose/kind-values.yaml`, which sets
`monitoring.webhookUrl` to the Compose backend. On `local`, kube-prometheus-stack also reads
`values/kube-prometheus-stack-local.yaml`, which has two settings:
- node-exporter without the host root mount, which Docker Desktop's VM can't provide.
- Prometheus on NodePort 30090, for Compose's k8s-mcp on kind's Docker network.

## Secrets

No values file, template, or environment holds a secret. The charts reference existing Secrets
by name, which `scripts/create-secrets.sh` creates in the `consoletrack` namespace:

| Secret | Keys | Read by |
|---|---|---|
| `consoletrack-api-key` | `anthropic-api-key` | worker |
| `consoletrack-auth` | `admin-password-hash`, `viewer-password-hash`, `session-secret` | backend |
| `consoletrack-webhook` | `token` | backend, and Alertmanager (the bearer token on the webhook) |
| `consoletrack-db-app` | `username`, `password` | backend, worker; created by CloudNativePG, not the script |

The Grafana admin password is generated by the Grafana chart into its own Secret.

**Release records are ConfigMaps.** Every Helm, Helmfile, and helm-diff run uses
`HELM_DRIVER=configmap`, so CI's deploy group needs no Secret access at all. The Makefile
exports it (and ignores any other value), `deploy.yaml` sets it, and `helmfile.yaml.gotmpl`
refuses to render without it. Run Helmfile directly only as `HELM_DRIVER=configmap helmfile …`:
with another driver Helm wouldn't find the existing releases.

## Checks (no cluster, no AWS)

```sh
make helm-lint   # helmfile lint + template for eks, eks with alb, and local, then helm-test on the output
make helm-test   # the rendered-manifest checks alone; renders our charts with plain helm without helmfile
```

`make helm-lint` is part of `make lint` and needs Helmfile (CI installs it). It renders three
configurations: `eks` and `eks-alb` with the placeholder Terraform outputs in `tests/values/`,
and `local`. `helm/tests/test_rendered.py` then checks every container of ours for requests and
limits, probes, `runAsNonRoot`, a read-only root filesystem, no privilege escalation, and dropped
capabilities; that Secrets are referenced by name only; that the app charts are namespaced only;
that `k8s-mcp`'s Roles are read-only without Secrets or ConfigMaps; that the remediator can only
get and patch Deployments; and that the ALB only serves HTTPS to `ALLOWED_CIDRS`.
