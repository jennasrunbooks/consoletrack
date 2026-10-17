# helm/charts/demo-app

The demo workload's Kubernetes side, in the `demo` namespace. Cluster faults (oom, bad-image,
bad-probe) are values toggles. A `tier=app` release in `helm/helmfile.yaml.gotmpl`. Its AWS side
is `terraform/demo-app/us-east-1`.

Filled by [#12](https://github.com/jennasrunbooks/consoletrack/issues/12) (Helm charts, Helmfile, and `make up`/`down`/`deploy`).
