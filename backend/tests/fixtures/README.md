# Test fixtures

- `cloudtrail/cli-put-role-policy.json`: the CLI `PutRolePolicy` event, copied from
  `terraform/consoletrack/us-east-1/tests/fixtures/events/matched/` (the EventBridge pattern
  tests). The agent tests serve it from a fake `aws-mcp`; when `aws-mcp` fixtures mode lands
  (#9), the same event comes from there. Placeholder account `123456789012` only.
