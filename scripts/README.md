# scripts

Shell helpers the Makefile calls: `bootstrap-state.sh` (state bucket, #3), `preflight.sh`,
`teardown-verify.sh`, `wait-for.sh`. Scripts never read or print secret values from `.env`.
