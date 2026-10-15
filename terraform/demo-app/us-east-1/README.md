# terraform/demo-app/us-east-1

The AWS side of the demo app: its IAM role and policy, S3 bucket, security group, and Pod
Identity association. These are what the demo scenarios drift on purpose and what the revert
workflow re-applies, so this root stays small and in its own state.

Filled by the cloud scenarios + revert workstream (no issue filed yet).
