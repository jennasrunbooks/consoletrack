# aws-mcp

Read-only FastMCP server (streamable HTTP) for CloudTrail lookups, IAM reads and policy
simulation, security group reads, and Terraform state address lookup. Runs under its own Pod
Identity role; a `fixtures` mode answers from recorded events for `make demo-replay`. The tool
list is in `product-spec.md` → Triage agent and guardrails.

Filled by the change intake + aws-mcp workstream (no issue filed yet).
