# Test fixtures

## `cloudtrail/`

EventBridge-wrapped CloudTrail events, written by hand from AWS's documented format (not from a
real account): placeholder account `123456789012`, documentation IP ranges, fake names. They
start from the EventBridge pattern tests' events
(`terraform/consoletrack/us-east-1/tests/fixtures/events/`), with a unique `eventID` each and
request parameters filled in. They're a shared contract: the parser tests
(`tests/unit/test_intake_parser.py` lists the expected `via`, `resource_arn`, and risk for each),
the intake integration test, `scripts/replay-fixtures.py`, the agent tests, and `aws-mcp`'s
fixtures mode (#9 PR 2) all read them. Replay sends every top-level file.

| File | Event | Expect |
|---|---|---|
| `console-authorize-sg-ingress.json` | Console `AuthorizeSecurityGroupIngress`, SSH from `0.0.0.0/0` on `demo-app`'s group (drift scenario) | change, `console`, high |
| `duplicate-console-authorize-sg-ingress.json` | The same CloudTrail event (same `eventID`) delivered again | no second change |
| `cli-put-role-policy.json` | CLI `PutRolePolicy` on `demo-app-role-use1-a1b2c3`, leaving only `s3:ListBucket` (identity scenario). Keeps its original `eventID`: the agent tests read it | change, `cli`, high (watched role) |
| `cli-delete-role-policy.json` | CLI `DeleteRolePolicy` removing that role's S3 read policy | change, `cli`, high |
| `sdk-put-role-policy.json` | Boto3 `PutRolePolicy` on `analytics-batch`, outside the watched path | change, `sdk`, medium |
| `laptop-terraform-modify-sg-rules.json` | Terraform from a laptop (SSO session): `ModifySecurityGroupRules`, HTTPS from anywhere | change, `terraform_outside_pipeline`, medium |
| `pipeline-role-without-gh-session.json` | The `ct-gh-apply` role used from the CLI with session `laptop-session` | change, `cli`, high |
| `gh-session-name-on-other-role.json` | A `gh-` session name on a non-pipeline role, RDP from `::/0` | change, `console`, high |
| `demo-developer-revoke.json` | `make demo-drift`'s `RevokeSecurityGroupIngress` (`ct-demo-developer` role) | change, `cli`, medium |
| `console-s3-aws-internal.json` | Console `PutBucketPolicy` (`invokedBy: AWS Internal`) | change, `console`, medium |
| `cloudshell-create-tags.json` | CloudShell CLI (`exec-env/CloudShell`) tagging the demo group | change, `cloudshell`, low |
| `canary-create-tags.json` | The canary role's `CreateTags` with `ct:canary` | heartbeat, no change |
| `excluded/*.json` | The pattern tests' excluded events: cluster roles, AWS-service callers, the pipeline's `gh-` session, a read, an unwatched service | `Ignored` (defense in depth) |
