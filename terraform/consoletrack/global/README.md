# terraform/consoletrack/global

Account-wide, long-lived root in the ConsoleTrack hub account: GitHub OIDC provider and the
plan/apply/deploy/revert roles (only with `ENABLE_GITHUB_OIDC=true`), the `ct` permissions
boundary, the demo "developer" role, the Route 53 zone when `DOMAIN` is set, and the optional
budget. Survives `make down`.

Filled by #3.
