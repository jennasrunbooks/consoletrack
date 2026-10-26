# grafana-dashboards

Dashboards for kube-prometheus-stack's Grafana, as ConfigMaps the sidecar picks up
(`grafana_dashboard: "1"`, any namespace). A `tier=addons` release in `monitoring`, installed on
`eks` only.

| Dashboard | From |
|---|---|
| **Incident journey** | `observability/grafana/dashboards/incident-journey.json`, the file `make local` mounts into its Grafana. `helm/helmfile.yaml.gotmpl` reads it into `dashboards`, so there is one copy. |
| Node Exporter / Nodes, Kubernetes / Compute Resources / Pod, Alertmanager / Overview | `files/`: the kube-prometheus-stack 91.9.0 dashboards of those names, kept when its default dashboards are turned off (`helm/values/kube-prometheus-stack-eks.yaml`). |

**Intake health** stays in the consoletrack chart.

To refresh `files/` after bumping kube-prometheus-stack, render the three templates with the
chart's values (`helm template … -s templates/grafana/dashboards-1.14/<name>.yaml`) and save each
ConfigMap's JSON (sorted keys, indented) under the file names here. The datasource variable
(`${datasource}`) resolves to the default Prometheus datasource.
