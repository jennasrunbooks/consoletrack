# helm/charts/gateway-api-crds

The upstream Gateway API CRDs at a pinned version, wrapped as a chart so Helmfile manages them
like every other release (`tier=addons`, installed before the Load Balancer Controller and the
`consoletrack` routes).

Filled by [#12](https://github.com/jennasrunbooks/consoletrack/issues/12) (Helm charts, Helmfile, and `make up`/`down`/`deploy`).
