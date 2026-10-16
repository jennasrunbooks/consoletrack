# app/intake

The change intake worker: SQS consumer, CloudTrail event parsing, and the canary heartbeat.
Filled by #9. It hands parsed events to `app.services.changes.record_change` (deduplicated on the
CloudTrail event ID) and reports queue, trail, and canary status through
`app.services.intake_health.IntakeStatusSource`.
