# helm/consoletrack

Chart for ConsoleTrack itself: backend, worker, frontend, both MCP servers, the canary CronJob,
Gateway API routes, RBAC, alert rules, the intake Grafana dashboard, and remediation policies.
Deployed by `make deploy` or CI, not Terraform.

Filled by the EKS + deploy pipeline workstream (no issue filed yet).
