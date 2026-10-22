# helm/environments

Helmfile environments are deployment targets, not apps. Releases are grouped with labels
(`tier=addons`, `tier=app`) instead.

- `eks.yaml`: the default, the EKS cluster from `terraform/consoletrack/us-east-1`.
- `local.yaml`: kind, without the Load Balancer Controller, ExternalDNS, or the canary; the
  intake replays recorded events and `aws-mcp` runs in fixtures mode.
- `values.generated.yaml`: written from `.env` and the Terraform outputs by the Makefile and
  layered on top of the file above. Gitignored; never commit it. It never holds a secret, but
  the queue URLs contain the account ID, which stays out of the repo.

## Keys

Every key the Helmfile reads. `eks.yaml` and `local.yaml` set each one (most empty), and
`values.generated.yaml` overrides whichever it sets.

| Key | From | Used by |
|---|---|---|
| `target` | `eks` or `local` (the environment files) | consoletrack (canary, validation), cluster-defaults |
| `exposure` | `EXPOSURE` | LBC and GatewayClass (`alb`), consoletrack Gateway |
| `domain` | `DOMAIN` | ExternalDNS (installed when set), Gateway and HTTPRoute hostnames |
| `allowedCidrs` | `ALLOWED_CIDRS` (a list, or the comma-separated string from `.env`) | the ALB's `sourceRanges` |
| `nameSuffix` | `NAME_SUFFIX` | ExternalDNS `txtOwnerId`; aws-mcp's state bucket `ct-tfstate-<suffix>` |
| `awsRegion` | `AWS_REGION` | LBC, ExternalDNS, the app |
| `watchedIamPaths` | `WATCHED_IAM_PATHS` | aws-mcp |
| `triageModel`, `triageModelEscalation` | `TRIAGE_MODEL`, `TRIAGE_MODEL_ESCALATION` | worker, backend |
| `grafanaUrl` | `GRAFANA_URL` | backend (intake panel link) |
| `clusterName` | `consoletrack/us-east-1` output `cluster_name` | LBC, aws-mcp |
| `vpcId` | output `vpc_id` | LBC (pods can't reach instance metadata to discover it) |
| `certificateArn` | ACM certificate for `DOMAIN` (`acm.tf`) | the ALB's HTTPS listener |
| `changesQueueUrl`, `changesDlqUrl` | outputs `changes_queue_url`, `changes_dlq_url` | worker |
| `trailName` | output `trail_name` | worker (`cloudtrail:GetTrailStatus`) |
| `canarySecurityGroupId` | output `canary_security_group_id` | canary CronJob |
| `demoBucket` | `demo-app/us-east-1` output `bucket_name`; empty until that root exists, and the demo app then reads no S3 object | demo-app |
| `imageTag` | the commit SHA in `deploy.yaml` (`--state-values-set imageTag=<sha>`); `local` on kind | consoletrack and demo-app images |
| `demoFault` | empty; `--state-values-set demoFault=oom` (or `badImage`, `badProbe`) for one run | demo-app's `faults.*`, and turns off wait and atomic for that sync |
| `awsMcpEnabled` | `false` in both environment files until aws-mcp ships (#9) | the aws-mcp Deployment and the worker's `AWS_MCP_URL` |
| `intakeMode` | `live` on eks, `replay` on local | worker, backend |
| `storageClass` | `gp3` on eks (cluster-defaults), `standard` on kind | Postgres volume |

`helm/tests/values/` holds placeholder values in the same shape for `make helm-lint`.

## How `values.generated.yaml` is written

`make helm-values` runs `scripts/helm-values.sh`; `make addons` and `make deploy` depend on it,
and `make down` runs it with only the `.env` keys. It writes a key only when it has a value, so
the default in `eks.yaml` applies otherwise, and the file is mode 0600. `HELM_VALUES_SOURCE`
picks where the Terraform outputs come from:

- `terraform` (default): `terraform output -json` of `consoletrack/us-east-1`, plus
  `demo-app/us-east-1` when it has `.tf` files.
- `env`: the variables `deploy.yaml` sets from GitHub variables and environment secrets
  (`CLUSTER_NAME`, `CHANGES_QUEUE_URL`, `CHANGES_DLQ_URL`, `TRAIL_NAME`, `CANARY_SECURITY_GROUP_ID`;
  optionally `VPC_ID`, `CERTIFICATE_ARN`, `DEMO_BUCKET`). The deploy role can't read Terraform state.
- `none`: the `.env` keys only.

`helm/tests/test_values_generated.py` checks that every key the script writes is declared in
`eks.yaml` and `local.yaml` and read by the Helmfile, and that it produces the placeholder
values' keys.
