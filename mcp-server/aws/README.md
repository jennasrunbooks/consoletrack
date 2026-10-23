# aws-mcp

Read-only FastMCP server the triage agent uses to see AWS: CloudTrail writes, IAM roles under the
watched paths and policy simulation, security groups, and the demo-app root's Terraform state.
Streamable HTTP on `:8080/mcp` in the cluster, under its own Pod Identity role
(`ct-aws-mcp-<region>-<suffix>`); stdio for local use against a sandbox profile. Spec:
`product-spec.md` → Triage agent and guardrails; permissions:
[docs/permissions.md](../../docs/permissions.md#aws-mcp-tools-apis-and-iam).

## Tools

All six are reads (`readOnlyHint: true`).

| Tool | Returns | Limits |
| --- | --- | --- |
| `get_resource_changes(resource_arn, hours)` | CloudTrail writes to the resource, newest first: event, time, principal, role, `via` (console, cloudshell, cli, terraform, sdk), source network, error code; user agent, session name, request parameters, and error message in `untrusted_data` | `hours` 1–72 (default 24); 20 events, 3 `LookupEvents` pages (150 events scanned) |
| `get_principal_activity(principal_arn, hours)` | The same, for writes by one caller: an assumed-role session (by session name, then matched on the full ARN), an IAM user, or every session of an IAM role | As above |
| `get_iam_role(role_name)` | Path, trust policy, inline policies, attached policies (documents only for those under a watched path; others by name), permissions boundary, last used | Roles under `WATCHED_IAM_PATHS` only; 10 inline and 10 attached policies |
| `simulate_access(role_name, actions, resources)` | Per action and resource: `allowed`, `implicitDeny`, or `explicitDeny`, the deciding statements (policy and position), missing context keys, the boundary's decision | Watched roles only; 1–10 actions, up to 10 resource ARNs (default `*`). Identity policies and the boundary only, no resource policies or SCPs |
| `get_security_group(group_id)` | Inbound and outbound rules (protocol, ports, CIDRs, prefix lists, referenced groups), `open_to_world` per rule; description and tags in `untrusted_data` | 100 rules per direction |
| `lookup_terraform_address(resource_arn)` | `matches`: the resource's address(es) and attributes in the demo-app root's state; `related`: resources whose attributes point at it (ingress rules, role policies) | The state object only; 10 of each; sensitive attributes dropped |

A role ARN without its path (how CloudTrail records `roleName`, and so how the intake stores it)
works: CloudTrail indexes roles by name, and the state lookup matches `aws_iam_role` by name.

## Guardrails

Each holds on its own if the prompt is subverted; IAM denies everything outside the role's
policy again in AWS.

- **One AWS wrapper with an allowlist** (`aws_mcp/aws.py`). `AwsReader.call(service, operation)`
  refuses anything outside `ALLOWED_OPERATIONS` before a request is built, and the same check is
  a botocore `before-parameter-build` hook on every client it creates, so a client used directly
  can't make an unlisted call either. Nothing else in the package imports boto3 or creates a
  client.
- **The state object only.** S3 `GetObject` is allowed only for `TF_STATE_BUCKET` and
  `demo-app/us-east-1/terraform.tfstate`; the server won't start with another `TF_STATE_KEY`.
- **Watched IAM paths in the server.** `get_iam_role` and `simulate_access` read the role, then
  refuse it unless its path starts with one of `WATCHED_IAM_PATHS`, before reading or simulating
  anything else (IAM already denies `GetRole` outside those paths; the denial gets the same
  refusal). Attached policies outside the paths are listed by name, never read. The server won't
  start without a watched path, or with `/`.
- **Redaction** (`aws_mcp/redact.py`) of every result: values under credential-like keys
  (`accessKeyId`, `secretAccessKey`, `sessionToken`, `password`, `credentials`, ...) are replaced
  whole; every string is scanned for access key IDs (`AKIA…`/`ASIA…`), STS session tokens,
  JWTs, provider tokens, `key=value` secrets, connection-string passwords, and PEM keys. The
  caller's source IP becomes its /24 (`198.51.100.x`; /48 for IPv6) everywhere in the event,
  including a `/32` rule for their own address. Account IDs stay (the agent needs ARNs).
- **Untrusted input.** CloudTrail records whatever the caller sent, so user agents, session
  names, request parameters, and error messages come back in `untrusted_data`, and security
  group descriptions and tags too; the server instructions and each result's `note` say to treat
  them as data.
- **Size caps** on every result: events per call, 4,000 characters per string, 100 items per
  list, 16 KiB per event (larger request parameters become a cut JSON string), 96 KiB per
  result, each with `truncated: true` when something was cut.
- **Rate limit.** `LookupEvents` allows about two calls per second per account. Identical
  lookups (tool and arguments) within `LOOKUP_CACHE_SECONDS` (120) come from memory
  (`cached: true`); botocore's adaptive retry mode backs off on throttling, and if it's still
  throttled the tool returns the events it has with `throttled: true` and a note to retry,
  instead of failing the run. Throttled results aren't cached.
- Unexpected exceptions are masked; only refusals and AWS error codes reach the model. Logs carry
  the tool name only, never ARNs or results.

What it can't guarantee: redaction is pattern-based, so a secret with no recognizable shape
inside free text (a request parameter named `note`) can get through; the backend redacts again
before storing evidence. `LookupEvents` and `DescribeSecurityGroups` are account-wide (see
docs/permissions.md → Accepted gaps).

## Configuration

The chart (`helm/charts/consoletrack/templates/aws-mcp.yaml`) sets every variable but the cache.

| Variable | Default | Meaning |
| --- | --- | --- |
| `WATCHED_IAM_PATHS` | required | Comma-separated, e.g. `/demo-app/`. Must match Terraform's `watched_iam_paths` |
| `AWS_REGION` | `us-east-1` | Region for CloudTrail and EC2 (IAM is global) |
| `TF_STATE_BUCKET` | empty | `ct-tfstate-<suffix>`; empty makes `lookup_terraform_address` return an error |
| `TF_STATE_KEY` | `demo-app/us-east-1/terraform.tfstate` | The only accepted value |
| `EKS_CLUSTER_NAME` | empty | Not used by a tool yet |
| `AWS_MCP_MODE` | `live` | `fixtures` isn't implemented yet: the server exits with a message |
| `LOOKUP_CACHE_SECONDS` | `120` | 0 turns the cache off |
| `AWS_MCP_TRANSPORT` | `http` | `http` or `stdio` |
| `AWS_MCP_HOST`, `AWS_MCP_PORT` | `0.0.0.0`, `8080` | HTTP listener |
| `TELEMETRY_EXPORTER` | `none` | `none`, `otlp`, or `console`, as k8s-mcp |

`/healthz` is liveness; `/readyz` checks that AWS credentials resolve (Pod Identity's token, or
a profile) without an API call, so the probe never spends `LookupEvents`' rate limit.

## Local use against a sandbox profile

On the host (the devcontainer has no AWS credentials by design), with a profile for a sandbox
account that has a read-only role like the Terraform one:

```bash
cd mcp-server/aws
AWS_PROFILE=sandbox-readonly WATCHED_IAM_PATHS=/demo-app/ TF_STATE_BUCKET=ct-tfstate-<suffix> \
  AWS_MCP_TRANSPORT=stdio uv run python -m aws_mcp
```

That's the command an MCP client (Claude Code's `.mcp.json`) runs over stdio; with
`AWS_MCP_TRANSPORT=http` it serves `http://localhost:8080/mcp` instead. The in-server checks
apply, but IAM is whatever the profile allows, so use a read-only profile, never an admin one.

## Tests

```bash
make test-aws-mcp   # moto (IAM, EC2, S3) and stubbed CloudTrail; no AWS, no network (part of make test)
make lint-aws-mcp   # ruff (part of make lint)
```

moto has no `LookupEvents` or `SimulatePrincipalPolicy`: CloudTrail goes through botocore's
`Stubber`, fed with the intake's recorded events (`backend/tests/fixtures/cloudtrail/`), and the
simulation through a canned response. `test_allowlist.py` walks the package for every operation
it names, checks each against the allowlist and against the actions in the `aws_mcp` policy in
`terraform/consoletrack/us-east-1/pod-identity.tf`, and calls unlisted operations through the
wrapper and the client. A fixture in `conftest.py` records every operation aws-mcp's clients send
in every test and fails the test on one outside the allowlist.

## Image

```bash
docker buildx build --platform linux/arm64 -t ghcr.io/jennasrunbooks/consoletrack-aws-mcp mcp-server/aws/
```

Runs as uid 10001, `python -m aws_mcp`, with a `HEALTHCHECK` on `/healthz`. `make images` builds
and pushes it with the others.
