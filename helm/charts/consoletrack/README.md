# helm/charts/consoletrack

Chart for ConsoleTrack itself: backend, worker, frontend, both MCP servers, the canary CronJob,
Gateway API routes, RBAC, alert rules, the intake Grafana dashboard, and remediation policies.
A `tier=app` release in `helm/helmfile.yaml.gotmpl`, applied by `make deploy` or CI
(`helmfile -l tier=app apply`), never by Terraform.

Filled by the EKS + deploy pipeline workstream (no issue filed yet).
