# helm/charts/cluster-defaults

Cluster-scoped objects ConsoleTrack needs that no third-party chart creates. A `tier=addons`
release, so only a human with cluster admin applies it (`make addons`): the CI deploy role is
namespaced and can't create cluster-scoped objects, which is why these aren't in the
`consoletrack` chart.

| Object | When | Why |
|---|---|---|
| StorageClass `gp3` (EBS CSI, encrypted, `WaitForFirstConsumer`, `Delete`) | `eks` environment | The CloudNativePG volume. EKS ships only the old `gp2` class; this one isn't made the default, so nothing else changes |
| GatewayClass `alb` (`gateway.k8s.aws/alb`) | `EXPOSURE=alb` | The Load Balancer Controller's ALB Gateway class, which the `consoletrack` Gateway names |

It installs after `gateway-api-crds` (`needs:` in `helm/helmfile.yaml.gotmpl`), because the
GatewayClass kind comes from those CRDs.
