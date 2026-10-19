# helm/charts/gateway-api-crds

The upstream Gateway API CRDs at a pinned version, wrapped as a chart so Helmfile manages them
like every other release (`tier=addons`, installed before the Load Balancer Controller,
ExternalDNS, and the `consoletrack` routes).

- `templates/standard-install.yaml` is the release's `standard-install.yaml`, byte for byte:
  the standard-channel CRDs plus the upstream `safe-upgrades` ValidatingAdmissionPolicy, which
  stops an experimental-channel install from replacing them.
- They are templates rather than `crds/`, so Helm upgrades them along with the version. Helm
  never upgrades anything in `crds/`.
- `Chart.yaml` `appVersion` names the upstream release; `version` follows it.

Current: **v1.6.2** (Sep 3, 2026), sha256
`faede450fa178126aba41337737b97d351ebe87d93c910237ce1e072d1ca40d9`.

## Bumping

1. Check that the Load Balancer Controller version in `helm/helmfile.yaml.gotmpl` supports the
   new Gateway API version (its release notes list it), and that the version is at least two
   weeks old.
2. Replace the file and check its checksum against the release's assets:

   ```sh
   v=v1.6.2
   curl -fsSLo helm/charts/gateway-api-crds/templates/standard-install.yaml \
     "https://github.com/kubernetes-sigs/gateway-api/releases/download/$v/standard-install.yaml"
   sha256sum helm/charts/gateway-api-crds/templates/standard-install.yaml
   ```

3. Set `version` and `appVersion` in `Chart.yaml`, update the line above, and run
   `make helm-lint`.

Removing this release deletes the CRDs and with them every Gateway and HTTPRoute in the cluster,
which is what `make down` wants (the Load Balancer Controller then deletes the ALB). Don't
uninstall it on its own while the app is running.
