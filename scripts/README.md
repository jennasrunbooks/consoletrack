# scripts

Shell helpers the Makefile calls: `bootstrap-state.sh` (state bucket, #3), `preflight.sh`,
`teardown-verify.sh`, `wait-for.sh`, `gh-env-secrets.sh` (sets each GitHub environment's
`AWS_ROLE_ARN` from Terraform outputs; run by a human, never the agent), `test-k8s.sh`
(`make test-k8s`: k8s-mcp against a throwaway kind cluster, in CI). Scripts never read or
print secret values from `.env`.
