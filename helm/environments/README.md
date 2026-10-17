# helm/environments

Helmfile environments are deployment targets, not apps. Releases are grouped with labels
(`tier=addons`, `tier=app`) instead.

- `eks.yaml`: the default, the EKS cluster from `terraform/consoletrack/us-east-1`.
- `local.yaml`: kind, without the Load Balancer Controller and ExternalDNS.
- `values.generated.yaml`: written from `.env` by the Makefile and read by
  `helm/helmfile.yaml.gotmpl` as environment values. Gitignored; never commit it.

Filled by [#12](https://github.com/jennasrunbooks/consoletrack/issues/12) (Helm charts, Helmfile, and `make up`/`down`/`deploy`).
