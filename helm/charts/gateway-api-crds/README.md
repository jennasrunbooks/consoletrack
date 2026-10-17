# helm/charts/gateway-api-crds

The upstream Gateway API CRDs at a pinned version, wrapped as a chart so Helmfile manages them
like every other release (`tier=addons`, installed before the Load Balancer Controller and the
`consoletrack` routes).

Filled by the EKS + deploy pipeline workstream (no issue filed yet).
