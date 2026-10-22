# scripts

Shell helpers the Makefile calls: `bootstrap-state.sh` (state bucket, #3), `preflight.sh`
(`make preflight`), `helm-values.sh` (`make helm-values`: `values.generated.yaml` from `.env`
and the Terraform outputs), `create-secrets.sh` (`make create-secrets`: the three Secrets, each
only if missing, values on stdin only), `images.sh` (`make images`: arm64 images to GHCR; also
run by `deploy.yaml`), `down-cluster.sh` (the Helmfile half of `make down`),
`teardown-verify.sh` (`make verify-clean`, and the wait inside `make down`),
`delete-state-bucket.sh` (`make down-all`), `gh-deploy-config.sh` (`make gh-deploy-config`: deploy.yaml's repo variables and secrets), `gh-env-secrets.sh` (sets each GitHub environment's
`AWS_ROLE_ARN` from Terraform outputs; run by a human, never the agent), `test-k8s.sh`
(`make test-k8s`: k8s-mcp against a throwaway kind cluster, in CI), `local-kind.sh` (the kind
half of `make local`: the `consoletrack` cluster, kube-prometheus-stack, demo-app, k8s-mcp's
kubeconfig, and Alertmanager pointed at the Compose backend). Scripts never read or print
secret values from `.env`.
