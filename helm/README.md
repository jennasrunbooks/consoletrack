# helm

Everything inside Kubernetes. Terraform is AWS-only; Helmfile owns every chart, third-party
and ConsoleTrack's own (`product-spec.md` → Terraform layout, Cluster add-ons).

```text
helm/
  helmfile.yaml.gotmpl    # repositories, every release (tier=addons|app labels), pinned chart versions, environments
  charts/
    consoletrack/         # our app chart
    demo-app/             # the demo workload (demo namespace)
    gateway-api-crds/     # pinned upstream Gateway API CRDs wrapped as a chart
  values/                 # values for third-party releases
  environments/           # eks.yaml (default), local.yaml (kind); values.generated.yaml from .env (gitignored)
```

- `make addons` applies the `tier=addons` releases; `make deploy` and CI apply only `tier=app`
  (`consoletrack`, `demo-app`).
- `make helm-lint` (part of `make lint`) runs `helmfile lint` once `helmfile.yaml.gotmpl` exists.
- Third-party chart versions are pinned in `helmfile.yaml.gotmpl` and bumped by hand: Dependabot
  only updates the local charts' `Chart.yaml` dependencies.

`helmfile.yaml.gotmpl` and the charts are filled by [#12](https://github.com/jennasrunbooks/consoletrack/issues/12) (Helm charts, Helmfile, and `make up`/`down`/`deploy`).
