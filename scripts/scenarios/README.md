# scripts/scenarios

Cloud demo scenarios run as the demo "developer" role so they show up in CloudTrail like a real
console edit: `drift.sh` (port 22 opened on the demo security group, `make demo-drift`) and
`identity.sh` (`s3:GetObject` removed from the demo app role, `make demo-identity`).

Filled by the cloud scenarios + revert workstream (no issue filed yet).
