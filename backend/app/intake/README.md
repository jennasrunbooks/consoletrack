# app/intake

The change intake: CloudTrail → EventBridge → SQS → this worker → a change record
(product-spec.md → Workflow and data model, Intake health and canary). It runs inside
`python -m app.worker` beside the triage loop, when `CHANGES_QUEUE_URL` is set.

| File | What it does |
|---|---|
| `parser.py` | Pure functions: an SQS body → `NewChange`, `CanaryHeartbeat`, or `Ignored(reason)`. `via`, `resource_arn`, and risk are table-driven (`VIA_RULES`, `resource_of`, `RISK_RULES`) |
| `consumer.py` | `ChangeConsumer`: long poll (20 s, batch 10), parse, `record_change` or `record_canary`, **commit, then delete**. `ChangeAnalysisLoop`: analyses `detected` changes from the database, one at a time |
| `health.py` | `HealthReporter`: every 60 s, `GetQueueAttributes` (queue and DLQ) and `GetTrailStatus` into the `intake_reports` row, which the API reads (`DbIntakeStatus`) |
| `replay.py` | Puts the recorded fixtures on a queue (`scripts/replay-fixtures.py`, the integration test) |

## Rules worth knowing

- **Delete after commit.** A body the parser can't read, or a database error, leaves the message
  in the queue. SQS redelivers it, and after five receives it moves to the DLQ, which turns
  intake health Down. A failed delete after a commit is harmless: the redelivery is a duplicate
  (`record_change` dedupes on the CloudTrail event ID) and is deleted then.
- **Ignored events** (defense in depth behind the EventBridge rule): unwatched services, reads,
  calls made by an AWS service other than `AWS Internal`, the cluster's own roles, pipeline
  sessions (`ct-gh-*` role with a `gh-*` session), and failed calls (`errorCode` set).
- **Canary:** a `ct-canary-*` role's `CreateTags` with only the `ct:canary` key on one security
  group (`CANARY_SECURITY_GROUP_ID` when set). Anything else from that role is a change.
- **Analysis is picked from the database:** the oldest `detected` change with no analysis and no
  run inside its lease (`TRIAGE_LEASE_SECONDS`), at most `CHANGE_ANALYSIS_MAX_RUNS` runs. A
  worker restart doesn't lose a change, and a run that crashed is retried after its lease. With
  one worker replica (the chart's default), nothing is analysed twice; with more, two workers can
  occasionally pick the same change before either run starts.
- **Untrusted input:** request parameters, the user agent, and the session name are caller
  controlled. Parameters are stored with credential-like keys replaced (`CREDENTIAL_KEYS`),
  strings run through `app.agents.redact`, each string capped at 1 KiB and the whole at 8 KiB
  (larger becomes an excerpt marked `_truncated`); the user agent is capped at 512 characters.
  Log lines carry SQS message IDs, change IDs, and fixed reasons, never a body or a database
  error's text (SQLAlchemy puts the statement's parameters there).
- **Watched IAM paths:** an IAM permission change under `WATCHED_IAM_PATHS` is high risk, outside
  it the normal medium (it's still recorded). A policy ARN carries its path; CloudTrail gives a
  role or user only by name, so by the naming convention `/demo-app/` watches `demo-app-*`.
  `aws-mcp` (#9 PR 2) can resolve the real path.
- **Oldest message age:** SQS has no queue attribute for it (`ApproximateAgeOfOldestMessage` is
  a CloudWatch metric, and the worker's role reads only the queue). The reporter derives it from
  the `SentTimestamp` of the messages received since its last report, and reports 0 for an empty
  queue. It's a lower bound while the worker keeps receiving.
- **Health fields:** each AWS call is separate, and a failed one is stored as null, so the
  endpoint says which part is unknown. A failing receive nulls the queue fields too. A report
  older than 3 minutes means the worker isn't running: Down.
- **AWS calls:** `sqs:ReceiveMessage`, `sqs:DeleteMessage`, `sqs:GetQueueAttributes`,
  `cloudtrail:GetTrailStatus`. Nothing else (the worker's role, `ct-worker-<region>-<suffix>`).

## Replay

`INTAKE_MODE=replay` reads ElasticMQ (`AWS_ENDPOINT_URL_SQS`, unsigned requests) instead of AWS,
and writes no health report: the indicator shows Replay. Compose's worker is set up for it; with
the stack up, `make replay-fixtures` sends `backend/tests/fixtures/cloudtrail/*.json` (see that
folder's README). `make demo-replay` wiring is #15.
